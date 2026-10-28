"""Standalone clone TCP/UDP inference fence. Never changes SSH, replicas or Docker.

Only three owned chains are replaced. Pre-DNAT protects published host ports;
INPUT/FORWARD cover local listeners and direct container paths in both families.
"""
import argparse
import fcntl
import ipaddress
import json
import os
from pathlib import Path
import re
import shlex
import socket
import subprocess
import time
import uuid

PORTS='8820,10150:10151,10161,11435,18820,18830,40000:40063,41000:41999'
RAW='MAAS-CLONE-EARLY'; HOST='MAAS-CLONE-HOST'; FWD='MAAS-CLONE-FWD'
ROOT=Path('/var/lib/maas-network-boundary')


def validate(cfg):
    if cfg.get('schema_version')!=1:raise ValueError('Invalid boundary schema')
    iface=cfg['interface']
    if not isinstance(iface,str) or not re.fullmatch(r'[a-zA-Z0-9_.-]{1,15}',iface) or iface=='lo':
        raise ValueError('Invalid LAN interface')
    values=cfg['controllers']
    if not isinstance(values,list) or not values:raise ValueError('Explicit controllers required')
    addresses=[]
    for value in values:
        a=ipaddress.IPv4Address(value)
        if not a.is_private or a.is_loopback or a.is_link_local or a.is_unspecified:
            raise ValueError('Private unicast IPv4 controller required')
        addresses.append(str(a))
    nets=[]
    for value in cfg.get('bridge_subnets',['172.17.0.0/16']):
        n=ipaddress.IPv4Network(value,strict=True)
        if not n.is_private or n.prefixlen==0:raise ValueError('Invalid private bridge subnet')
        nets.append(str(n))
    return {**cfg,'controllers':sorted(set(addresses)),'bridge_subnets':nets}


def render(cfg,family):
    cfg=validate(cfg)
    allow=[f'-i {cfg["interface"]} -s {a}/32 -j RETURN' for a in cfg['controllers']] if family==4 else []
    raw=[f'-A {RAW} -i lo -j RETURN']+[f'-A {RAW} '+r for r in allow]
    for proto in ('tcp','udp'):
        raw.append(f'-A {RAW} -m addrtype --dst-type LOCAL -p {proto} -m multiport --dports {PORTS} -j DROP')
        if family==4:
            raw.extend(f'-A {RAW} -d {n} -p {proto} -m multiport --dports {PORTS} -j DROP' for n in cfg['bridge_subnets'])
    host=[f'-A {HOST} -i lo -j RETURN']+[f'-A {HOST} '+r for r in allow]
    host.append(f'-A {HOST} -j REJECT')
    fwd=[f'-A {FWD} '+r for r in allow]
    for proto in ('tcp','udp'):
        fwd.append(f'-A {FWD} -p {proto} -m multiport --dports {PORTS} -j REJECT')
    fwd.append(f'-A {FWD} -j RETURN')
    return {'raw':raw,'host':host,'forward':fwd}


def run(*args,check=True,input=None):
    return subprocess.run(args,capture_output=True,text=True,check=check,input=input,timeout=15)


def atomic(path,value):
    path.parent.mkdir(mode=0o700,parents=True,exist_ok=True)
    tmp=path.with_suffix('.tmp');fd=os.open(tmp,os.O_CREAT|os.O_TRUNC|os.O_WRONLY,0o600)
    with os.fdopen(fd,'w') as f:json.dump(value,f);f.flush();os.fsync(f.fileno())
    os.replace(tmp,path)


def hooks(binary,table,parent,chain,extra=()):
    spec=[*extra,'-j',chain]
    if run(binary,'-w','3','-t',table,'-C',parent,*spec,check=False).returncode:
        run(binary,'-w','3','-t',table,'-I',parent,'1',*spec)
    # Keep the raw/forward hook first and both INPUT protocol hooks ahead of permissive rules.
    entries=[r for r in run(binary,'-t',table,'-S',parent).stdout.splitlines() if r.startswith('-A ')]
    expected='-A '+parent+' '+' '.join(spec)
    if expected not in entries[:2 if parent=='INPUT' else 1]:
        run(binary,'-w','3','-t',table,'-I',parent,'1',*spec)
    entries=[r for r in run(binary,'-t',table,'-S',parent).stdout.splitlines() if r.startswith('-A ')]
    seen=False
    duplicates=[]
    for index,row in enumerate(entries,1):
        if row==expected:
            if seen:duplicates.append(index)
            seen=True
    for index in reversed(duplicates):
        run(binary,'-w','3','-t',table,'-D',parent,str(index))



def apply(cfg):
    for family,binary in ((4,'iptables'),(6,'ip6tables')):
        value=render(cfg,family)
        for table,chain,rows in [('raw',RAW,value['raw']),('filter',HOST,value['host']),('filter',FWD,value['forward'])]:
            if run(binary,'-t',table,'-S',chain,check=False).returncode:
                run(binary,'-w','3','-t',table,'-N',chain)
            text='*'+table+'\n-F '+chain+'\n'+'\n'.join(rows)+'\nCOMMIT\n'
            run(binary+'-restore','-w','3','--noflush',input=text)
        hooks(binary,'raw','PREROUTING',RAW)
        for proto in ('tcp','udp'):
            hooks(binary,'filter','INPUT',HOST,('-p',proto,'-m','multiport','--dports',PORTS))
        if run(binary,'-t','filter','-S','DOCKER-USER',check=False).returncode==0:
            hooks(binary,'filter','DOCKER-USER',FWD)
    return status(cfg)


def status(cfg):
    valid=True; forward={}
    for family,binary in ((4,'iptables'),(6,'ip6tables')):
        value=render(cfg,family)
        for table,rows in [('raw',value['raw']),('filter',value['host']),('filter',value['forward'])]:
            for row in rows:
                parts=shlex.split(row)[1:]
                valid=run(binary,'-t',table,'-C',*parts,check=False).returncode==0 and valid
        valid=run(binary,'-t','raw','-C','PREROUTING','-j',RAW,check=False).returncode==0 and valid
        for proto in ('tcp','udp'):
            valid=run(binary,'-t','filter','-C','INPUT','-p',proto,'-m','multiport','--dports',PORTS,'-j',HOST,check=False).returncode==0 and valid
        present=run(binary,'-t','filter','-S','DOCKER-USER',check=False).returncode==0
        forward[str(family)]=present
        if present:valid=run(binary,'-t','filter','-C','DOCKER-USER','-j',FWD,check=False).returncode==0 and valid
    if not valid:raise RuntimeError('Clone boundary rules are incomplete')
    return {'ok':True,'controllers':cfg['controllers'],'interface':cfg['interface'],
            'protected_ports':PORTS,'docker_user_present':forward,'ssh_rules_modified':False}


def rollback():
    for binary in ('iptables','ip6tables'):
        for table,parent,target in [('raw','PREROUTING',RAW),('filter','INPUT',HOST),('filter','DOCKER-USER',FWD)]:
            listing=run(binary,'-t',table,'-S',parent,check=False)
            for line in listing.stdout.splitlines():
                parts=shlex.split(line)
                if parts[:2]==['-A',parent] and parts[-2:]==['-j',target]:
                    run(binary,'-w','3','-t',table,'-D',*parts[1:])
        for table,chain in [('raw',RAW),('filter',HOST),('filter',FWD)]:
            run(binary,'-w','3','-t',table,'-F',chain,check=False)
            run(binary,'-w','3','-t',table,'-X',chain,check=False)
    run('systemctl','disable','maas-clone-boundary.service',check=False)
    drop=Path('/etc/systemd/system/maas-worker-bootstrap.service.d/20-clone-boundary.conf')
    if drop.exists():drop.unlink()
    run('systemctl','daemon-reload',check=False)
    return {'rolled_back':True,'only_owned_chains_removed':True,'ssh_preserved':True}


def main():
    p=argparse.ArgumentParser();p.add_argument('action',choices=['plan','guarded-apply','apply','confirm','status','rollback']);p.add_argument('--policy',default='/etc/maas/clone-boundary.json');a=p.parse_args()
    if os.getuid()!=0 or socket.gethostname().lower()=='xz-31-001' or socket.gethostname().lower().startswith('tower'):
        raise RuntimeError('Root on a worker clone only')
    cfg=validate(json.loads(Path(a.policy).read_text()))
    if cfg.get('hostname') and socket.gethostname()!=cfg['hostname']:raise RuntimeError('Node identity mismatch')
    if a.action=='plan':print(json.dumps({'policy':cfg,'rules':{str(f):render(cfg,f) for f in (4,6)}}));return
    ROOT.mkdir(mode=0o700,exist_ok=True)
    lock=os.open(ROOT/'lock',os.O_CREAT|os.O_RDWR,0o600)
    with os.fdopen(lock,'w') as handle:
        fcntl.flock(handle,fcntl.LOCK_EX)
        state=ROOT/'pending.json'
        if a.action=='guarded-apply':
            if state.exists() and json.loads(state.read_text()).get('pending'):raise RuntimeError('Existing boundary rollback is armed')
            unit='maas-clone-boundary-rollback-'+uuid.uuid4().hex[:10]
            atomic(state,{'pending':True,'unit':unit,'armed_at':time.time()})
            run('systemd-run','--quiet','--unit',unit,'--on-active=180s','/usr/bin/python3',str(Path(__file__).resolve()),'rollback','--policy',a.policy)
        if a.action=='rollback':value=rollback();atomic(state,{'pending':False,'rolled_back':True})
        elif a.action=='confirm':
            value=status(cfg);old=json.loads(state.read_text());run('systemctl','stop',old['unit']+'.timer')
            atomic(state,{**old,'pending':False,'confirmed_at':time.time()});value['confirmed']=True
        elif a.action=='status':value=status(cfg)
        else:value=apply(cfg)
        print(json.dumps(value))


if __name__=='__main__':main()
