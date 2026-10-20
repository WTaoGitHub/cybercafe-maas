"""Scoped P3 network boundary for the two existing workers. Never flush global rules.

Root-only; protects host ports and Docker destinations, preserving SSH. Existing
loopback proxies/tunnels remain trusted operator paths and must be audited separately.
"""
import argparse,ipaddress,json,os,socket,subprocess,time
from pathlib import Path
ROOT=Path('/root/wtaobj/maas-boundary');STATE=ROOT/'state.json'
HOST='MAAS-P3-HOST';DOCKER='MAAS-P3-DOCKER'
PORTS='8820,10150:10151,10161,11435,18080,18830,40000:40063,41000:41999'
NODES={'XZ-31-001':'10.11.31.1','XZ-31-002':'10.11.31.2'}
CONTAINERS={'wta-gpustack-server':80,'cybercafe-ocr':8820,'cybercafe-h3':11435}


def run(args,check=True,input=None):
    return subprocess.run(args,input=input,text=True,capture_output=True,check=check,timeout=15)


def write(value):
    ROOT.mkdir(mode=0o700,exist_ok=True)
    temp=STATE.with_suffix('.tmp');fd=os.open(temp,os.O_WRONLY|os.O_CREAT|os.O_TRUNC,0o600)
    with os.fdopen(fd,'w') as f:json.dump(value,f);f.flush();os.fsync(f.fileno())
    os.replace(temp,STATE)


def inventory():
    hostname=socket.gethostname()
    if hostname not in NODES:raise RuntimeError('Only the existing two workers are allowed')
    ip=NODES[hostname]
    if ip not in run(['hostname','-I']).stdout.split():raise RuntimeError('Node identity mismatch')
    route=json.loads(run(['ip','-j','route','get','10.11.31.2' if ip.endswith('.1') else '10.11.31.1']).stdout)[0]
    interface=route['dev']
    if not interface.replace('-','').replace('.','').isalnum():raise RuntimeError('Unexpected LAN interface')
    destinations=[];trusted_bridge=[]
    for name,port in CONTAINERS.items():
        found=run(['docker','inspect',name],False)
        if found.returncode:continue
        c=json.loads(found.stdout)[0]
        if not c['State']['Running']:continue
        for network in c['NetworkSettings']['Networks'].values():
            if network.get('GlobalIPv6Address'):
                raise RuntimeError('IPv6 Docker routing requires a reviewed forwarding policy')
            address=network.get('IPAddress')
            if address:
                address=str(ipaddress.IPv4Address(address));destinations.append({'name':name,'ip':address,'port':port})
                if name=='wta-gpustack-server':trusted_bridge.append(address)
    return {'node':hostname,'ip':ip,'interface':interface,'destinations':destinations,'trusted_bridge':trusted_bridge}


def chain_rules(inv,family):
    """No blanket ESTABLISHED accept: existing untrusted inference flows are fenced too."""
    lines=[f'-A {HOST} -i lo -j RETURN']
    if family==4:
        for address in NODES.values():
            lines.append(f'-A {HOST} -i {inv["interface"]} -s {address}/32 -j RETURN')
        for address in inv['trusted_bridge']:
            lines.append(f'-A {HOST} -i docker0 -s {address}/32 -j RETURN')
    lines.append(f'-A {HOST} -p tcp -j REJECT --reject-with tcp-reset')
    if family==4:
        for address in NODES.values():lines.append(f'-A {DOCKER} -i {inv["interface"]} -s {address}/32 -j RETURN')
        for address in inv['trusted_bridge']:lines.append(f'-A {DOCKER} -i docker0 -s {address}/32 -j RETURN')
        lines.append(f'-A {DOCKER} -p tcp -j REJECT --reject-with tcp-reset')
    return lines


def hooks(inv,family):
    result=[['INPUT','-p','tcp','-m','multiport','--dports',PORTS,'-j',HOST]]
    if family==4:
        # Docker DNAT changes dport to the container port before DOCKER-USER.
        for port in (8820,11435,18080):
            result.append(['DOCKER-USER','-p','tcp','-m','conntrack','--ctdir','ORIGINAL','--ctorigdst',inv['ip'],'--ctorigdstport',str(port),'-j',DOCKER])
        # Also fence direct routed access to published containers' bridge IPs.
        for d in inv['destinations']:
            result.append(['DOCKER-USER','-p','tcp','-d',d['ip']+'/32','--dport',str(d['port']),'-j',DOCKER])
    return result


def exists(binary,rule):return run([binary,'-w','3','-C',*rule],False).returncode==0


def hooks_first(text,count,target):
    rules=[r for r in text.splitlines() if r.startswith('-A ')]
    return len(rules)>=count and all(r.split()[-2:]==['-j',target] for r in rules[:count])


def restore():
    if not STATE.exists():return {'restored':True,'no_state':True}
    state=json.loads(STATE.read_text())
    if state['inventory']['node']!=socket.gethostname():raise RuntimeError('Restore identity mismatch')
    for family in (4,6):
        binary='iptables' if family==4 else 'ip6tables'
        for rule in state['hooks'][str(family)]:
            while exists(binary,rule):run([binary,'-w','3','-D',*rule])
        for chain in ([HOST,DOCKER] if family==4 else [HOST]):
            if run([binary,'-w','3','-S',chain],False).returncode==0:
                run([binary,'-w','3','-F',chain]);run([binary,'-w','3','-X',chain])
    state['active']=False;write(state)
    run(['systemctl','stop',state['timer']+'.timer'],False)
    return {'restored':True}


def apply():
    inv=inventory()
    if STATE.exists() and json.loads(STATE.read_text()).get('active'):raise RuntimeError('Boundary already active; inspect or restore first')
    for family in (4,6):
        binary='iptables' if family==4 else 'ip6tables'
        for chain in ([HOST,DOCKER] if family==4 else [HOST]):
            if run([binary,'-w','3','-S',chain],False).returncode==0:raise RuntimeError('Owned chain name collision')
    if run(['iptables','-w','3','-S','DOCKER-USER'],False).returncode:raise RuntimeError('Expected Docker forwarding boundary unavailable')
    state={'active':True,'confirmed':False,'inventory':inv,'hooks':{str(f):hooks(inv,f) for f in (4,6)},
        'timer':'maas-boundary-rollback','applied_at':time.time()}
    write(state)
    run(['systemd-run','--quiet','--unit',state['timer'],'--on-active=180s','/usr/bin/python3',str(Path(__file__).resolve()),'restore'])
    try:
        for family in (4,6):
            binary='iptables' if family==4 else 'ip6tables'
            chains=[HOST,DOCKER] if family==4 else [HOST]
            for chain in chains:run([binary,'-w','3','-N',chain])
            for line in chain_rules(inv,family):run([binary,'-w','3',*line.split()])
            for rule in state['hooks'][str(family)]:run([binary,'-w','3','-I',rule[0],'1',*rule[1:]])
        return status()
    except BaseException:
        restore();raise


def status():
    state=json.loads(STATE.read_text());inv=inventory();rules_ok=True;observed={}
    for family in (4,6):
        binary='iptables' if family==4 else 'ip6tables'
        for rule in state['hooks'][str(family)]:rules_ok=exists(binary,rule) and rules_ok
        # A newly inserted ACCEPT/RETURN before our hooks can bypass otherwise
        # intact chains. Presence alone is not a boundary attestation.
        for parent,target in ([('INPUT',HOST),('DOCKER-USER',DOCKER)] if family==4 else [('INPUT',HOST)]):
            count=sum(r[0]==parent for r in state['hooks'][str(family)])
            listing=run([binary,'-w','3','-S',parent],False)
            rules_ok=listing.returncode==0 and hooks_first(listing.stdout,count,target) and rules_ok
        for chain in ([HOST,DOCKER] if family==4 else [HOST]):
            found=run([binary,'-w','3','-S',chain],False);observed[str(family)+'/'+chain]=found.stdout
            expected=[r for r in chain_rules(state['inventory'],family) if r.split()[1]==chain]
            # -S normalizes port numbers/protocol matches; verify complete installed
            # rule text via the kernel's -C parser, and reject extra rules.
            actual=[r for r in found.stdout.splitlines() if r.startswith('-A ')]
            rules_ok=not found.returncode and len(actual)==len(expected) and rules_ok
            for r in expected:rules_ok=exists(binary,r.split()[1:]) and rules_ok
    return {'active':state['active'],'confirmed':state['confirmed'],'rules_ok':rules_ok,
        'inventory_matches':inv==state['inventory'],'inventory':inv,'rules':observed,
        'scope':'network clients only; trusted nodes, loopback and existing local tunnels are operator paths',
        'safe_to_poweroff':False}


def main():
    p=argparse.ArgumentParser();p.add_argument('operation',choices=['apply','status','confirm','restore']);a=p.parse_args()
    if a.operation=='apply':value=apply()
    elif a.operation=='restore':value=restore()
    elif a.operation=='confirm':
        value=status()
        if not value['active'] or not value['rules_ok'] or not value['inventory_matches']:raise RuntimeError('Boundary verification failed')
        state=json.loads(STATE.read_text());run(['systemctl','stop',state['timer']+'.timer']);state['confirmed']=True;write(state);value=status()
    else:value=status()
    print(json.dumps(value))


if __name__=='__main__':main()
