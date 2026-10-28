"""Forced-command actuator for explicitly bound current clones. No VM/daemon operations."""
import argparse
import csv
import fcntl
import hashlib
import io
import json
import os
from pathlib import Path
import re
import shlex
import socket
import subprocess
import time

ROOT=Path('/var/lib/maas-node-maintenance')
POLICY=Path('/etc/maas/maintenance-node.json')
WORKER='maas-gpustack-worker'


def run(*args,check=True):
    return subprocess.run(args,capture_output=True,text=True,check=check,timeout=25)


def read(path):
    if path.is_symlink() or path.stat().st_mode&0o077:raise RuntimeError('Private regular policy/state required')
    return json.loads(path.read_text())


def write(path,value):
    path.parent.mkdir(mode=0o700,parents=True,exist_ok=True)
    tmp=path.with_suffix('.tmp');fd=os.open(tmp,os.O_CREAT|os.O_WRONLY|os.O_TRUNC,0o600)
    with os.fdopen(fd,'w') as f:json.dump(value,f);f.flush();os.fsync(f.fileno())
    os.replace(tmp,path)


def guard():
    if os.getuid()!=0 or socket.gethostname().lower()=='xz-31-001':raise RuntimeError('Worker root only')
    cfg=read(POLICY)
    bootstrap=json.loads(Path('/etc/maas/bootstrap.json').read_text())
    data=Path(bootstrap['worker_data_dir']);state=Path(bootstrap['state_dir'])
    applied=json.loads((state/'applied.json').read_text())
    worker=json.loads(run('docker','inspect',WORKER).stdout)[0]
    actual={'hostname':socket.gethostname(),'boot_id':Path('/proc/sys/kernel/random/boot_id').read_text().strip(),
            'container_id':worker['Id'],'image_id':worker['Image'],
            'worker_id':applied['worker_id'],'worker_name':applied['worker_name'],
            'native_uuid_sha256':hashlib.sha256((data/'worker_uuid').read_bytes()).hexdigest()}
    if any(cfg.get(k)!=v for k,v in actual.items()):raise RuntimeError('Clone identity changed; refuse stale maintenance binding')
    if cfg['address'] not in run('hostname','-I').stdout.split():raise RuntimeError('Clone address changed')
    return cfg,worker


def inventory():
    ids=run('docker','ps','-q').stdout.split()
    containers=json.loads(run('docker','inspect',*ids).stdout) if ids else []
    workloads=[];workers=[]
    for row in containers:
        name=row['Name'].lstrip('/');host=row['HostConfig']
        gpu=any('gpu' in cap for r in host.get('DeviceRequests') or [] for cap in r.get('Capabilities') or [])
        if 'gpustack' in name and 'worker' in name:workers.append(name)
        if '-run-' in name or gpu or host.get('Runtime')=='nvidia':workloads.append(name)
    raw=run('nvidia-smi','--query-compute-apps=pid,process_name,used_gpu_memory','--format=csv,noheader,nounits').stdout
    processes=[]
    for row in csv.reader(io.StringIO(raw)):
        if not row:continue
        if len(row)!=3 or not row[0].strip().isdigit():raise RuntimeError('GPU inventory unavailable')
        processes.append({'pid':int(row[0]),'name':row[1].strip(),'memory_mib':row[2].strip()})
    return {'running_worker_containers':workers,'running_workload_containers':workloads,
            'gpu_compute_processes':processes,'backend_stop_observed':not(workers or workloads or processes),
            'vm_powered_off':False,'safe_to_poweroff':False}


def main():
    p=argparse.ArgumentParser();p.add_argument('action',choices=['ssh','status','arm','stop','restore','disarm']);p.add_argument('session',nargs='?');a=p.parse_args()
    if a.action=='ssh':
        tokens=shlex.split(os.environ.get('SSH_ORIGINAL_COMMAND',''))
        if not 1<=len(tokens)<=2 or tokens[0] not in ('status','arm','stop','restore','disarm'):raise RuntimeError('Unsupported maintenance command')
        a.action=tokens[0];a.session=tokens[1] if len(tokens)==2 else None
    if a.action!='status' and (not a.session or not re.fullmatch(r'[a-f0-9]{32}',a.session)):
        raise RuntimeError('Explicit controller session required')
    cfg,worker=guard()
    ROOT.mkdir(mode=0o700,exist_ok=True)
    fd=os.open(ROOT/'lock',os.O_CREAT|os.O_RDWR,0o600)
    with os.fdopen(fd,'w') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX)
        if a.action=='status':
            value={**inventory(),'identity_verified':True,'worker_id':cfg['worker_id'],'boot_id':cfg['boot_id'],'container_id':worker['Id']}
            if (ROOT/'active.json').exists():
                active=read(ROOT/'active.json')['session'];prior=read(ROOT/(active+'.json'))
                value.update(maintenance_session=active,node_session_active=prior['active'])
        else:
            state_path=ROOT/(a.session+'.json')
            if a.action=='arm':
                active=ROOT/'active.json'
                if active.exists():
                    prior=read(ROOT/(read(active)['session']+'.json'))
                    if prior.get('active'):raise RuntimeError('Another node maintenance session is active')
                if state_path.exists():raise RuntimeError('Session already armed; inspect existing journal')
                if not worker['State']['Running']:raise RuntimeError('Baseline worker not running')
                state={'session':a.session,'active':True,'worker_id':cfg['worker_id'],
                       'container_id':worker['Id'],'restart_policy':worker['HostConfig']['RestartPolicy'],
                       'timer':'maas-node-restore-'+a.session[:12], 'armed_at':time.time()}
                write(state_path,state);write(active,{'session':a.session})
                run('systemd-run','--quiet','--unit',state['timer'],'--on-active=600s','/usr/bin/python3',str(Path(__file__).resolve()),'restore',a.session)
                value={'armed':True,'worker_id':cfg['worker_id'],'rollback_after_s':600}
            else:
                state=read(state_path)
                if not state['active']:raise RuntimeError('Node session is no longer active')
                if state['container_id']!=worker['Id']:raise RuntimeError('Worker container changed')
                if a.action=='stop':
                    before=inventory()
                    if set(before['running_workload_containers'])-{WORKER} or before['gpu_compute_processes']:
                        raise RuntimeError('Workloads/GPU activity remain; do not stop only the worker')
                    if worker['State']['Running']:run('docker','stop','-t','10',worker['Id'])
                    value=inventory()
                    if not value['backend_stop_observed']:raise RuntimeError('Actual node stop is unconfirmed')
                    state['stopped_at']=time.time();state['stop_evidence']=value;write(state_path,state)
                elif a.action=='restore':
                    if not worker['State']['Running']:run('docker','start',worker['Id'])
                    cfg,current=guard()
                    if current['HostConfig']['RestartPolicy']!=state['restart_policy']:raise RuntimeError('Worker restart policy drift')
                    state['restored_at']=time.time();write(state_path,state)
                    value={'restored':True,'worker_id':cfg['worker_id'],'container_id':current['Id'],'running':current['State']['Running']}
                else:
                    if not worker['State']['Running']:raise RuntimeError('Worker must run before disarming rollback')
                    run('systemctl','stop',state['timer']+'.timer',check=False)
                    state['active']=False;state['disarmed_at']=time.time();write(state_path,state)
                    value={'disarmed':True,'worker_id':cfg['worker_id']}
        print(json.dumps(value))


if __name__=='__main__':main()
