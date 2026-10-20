"""Fault actuator for the EXISTING XZ-31-002 worker. No worker/daemon creation."""
import argparse,json,os,socket,subprocess,time,uuid
from pathlib import Path
ROOT=Path('/root/wtaobj/maas-offline-002')
STATE=ROOT/'active.json'
CHAIN='MAAS002OFFLINE'
PREFIX='maas-ocr-002-offline-'


def run(*args,check=True):
    return subprocess.run(args,check=check,capture_output=True,text=True,timeout=20)


def guard():
    if socket.gethostname()!='XZ-31-002' or '10.11.31.2' not in run('hostname','-I').stdout.split():
        raise RuntimeError('Fault actuator is restricted to existing XZ-31-002')


def worker():
    x=json.loads(run('docker','inspect','wta-gpustack-worker').stdout)[0]
    return {'id':x['Id'],'running':x['State']['Running'],'image':x['Image']}


def write(state):
    ROOT.mkdir(mode=0o700,exist_ok=True)
    temp=STATE.with_suffix('.tmp');fd=os.open(temp,os.O_WRONLY|os.O_CREAT|os.O_TRUNC,0o600)
    with os.fdopen(fd,'w') as f:json.dump(state,f);f.flush();os.fsync(f.fileno())
    os.replace(temp,STATE)


def scoped_containers():
    return [n for n in run('docker','ps','--format','{{.Names}}').stdout.splitlines() if n.startswith(PREFIX) and n.endswith('-run-0')]


def remove_partition():
    for hook,flag in [('INPUT','-s'),('OUTPUT','-d')]:
        args=['iptables','-w',hook,flag,'10.11.31.1','-j',CHAIN]
        if run(*args[:2],'-C',*args[2:],check=False).returncode==0:
            run(*args[:2],'-D',*args[2:])
    if run('iptables','-w','-S',CHAIN,check=False).returncode==0:
        run('iptables','-w','-F',CHAIN);run('iptables','-w','-X',CHAIN)


def restore():
    guard()
    if not STATE.exists():return {'restored':True,'no_active_state':True}
    state=json.loads(STATE.read_text())
    if worker()['id']!=state['worker']['id']:raise RuntimeError('Existing worker identity changed; refusing blind restoration')
    remove_partition()
    for name in state.get('paused',[]):
        if name.startswith(PREFIX):run('docker','unpause',name,check=False)
    if state['worker']['running']:run('docker','start','wta-gpustack-worker')
    state.update(active=False,restored_at=time.time());write(state)
    run('systemctl','stop',state['timer']+'.timer',check=False)
    return {'restored':True,'worker':worker(),'state':state}


def arm(action,pause=False,timeout=180):
    guard()
    if STATE.exists() and json.loads(STATE.read_text()).get('active'):raise RuntimeError('A previous node fault is still active')
    if action not in ('stop','kill','partition') or not 60<=timeout<=240:raise ValueError('Invalid fault scope or recovery deadline')
    if run('iptables','-w','-S',CHAIN,check=False).returncode==0:raise RuntimeError('Fault chain already exists')
    original=worker()
    if not original['running']:raise RuntimeError('Existing worker is not running at baseline')
    state={'active':True,'action':action,'worker':original,'paused':[],'armed_at':time.time(),
           'timer':'maas-002-restore-'+uuid.uuid4().hex[:10]}
    write(state)
    run('systemd-run','--quiet','--unit',state['timer'],'--on-active='+str(timeout)+'s',
        '/usr/bin/python3',str(Path(__file__).resolve()),'restore')
    if pause:
        names=scoped_containers()
        if len(names)!=1:restore();raise RuntimeError('Expected exactly one temporary test inference container')
        # Persist before pausing so the watchdog can always undo this action.
        state['paused']=names;write(state)
        run('docker','pause',names[0])
    return {'armed':True,'state':state}


def apply():
    guard();state=json.loads(STATE.read_text())
    if not state['active'] or worker()['id']!=state['worker']['id']:raise RuntimeError('Unarmed fault or worker identity mismatch')
    action=state['action']
    if action=='stop':run('docker','stop','-t','8','wta-gpustack-worker')
    elif action=='kill':run('docker','kill','--signal','KILL','wta-gpustack-worker')
    elif action=='partition':
        run('iptables','-w','-N',CHAIN)
        # Preserve SSH in both directions, including the local ProxyJump tunnel.
        run('iptables','-w','-A',CHAIN,'-p','tcp','--dport','22','-j','RETURN')
        run('iptables','-w','-A',CHAIN,'-p','tcp','--sport','22','-j','RETURN')
        run('iptables','-w','-A',CHAIN,'-j','DROP')
        run('iptables','-w','-I','INPUT','1','-s','10.11.31.1','-j',CHAIN)
        run('iptables','-w','-I','OUTPUT','1','-d','10.11.31.1','-j',CHAIN)
    state['applied_at']=time.time();write(state)
    return {'applied':action,'worker':worker(),'scoped_containers':scoped_containers()}


def terminate_test_backend():
    guard()
    if worker()['running']:run('docker','stop','-t','8','wta-gpustack-worker')
    if worker()['running']:raise RuntimeError('Worker could still recreate the test backend')
    names=scoped_containers()
    for name in names:run('docker','rm','-f',name)
    if scoped_containers():raise RuntimeError('Test inference processes still present')
    return {'worker_stopped':True,'test_backend_processes_removed':names,'remaining_test_backends':[]}


def main():
    p=argparse.ArgumentParser();p.add_argument('operation',choices=['arm','apply','restore','terminate-test-backend','status','inventory'])
    p.add_argument('--action',choices=['stop','kill','partition']);p.add_argument('--pause',action='store_true');p.add_argument('--timeout',type=int,default=180);a=p.parse_args()
    guard()
    if a.operation=='arm':value=arm(a.action,a.pause,a.timeout)
    elif a.operation=='apply':value=apply()
    elif a.operation=='restore':value=restore()
    elif a.operation=='terminate-test-backend':value=terminate_test_backend()
    elif a.operation=='inventory':
        import sys
        sys.path.insert(0,'/root/wtaobj/maas-framework')
        from maas_platform.inventory import collect_inventory
        value=collect_inventory('XZ-31-002','quay.io/cybercafe-maas-ocr:p2-r1','maas-ocr-002-offline')
    else:
        value={'worker':worker(),'scoped_containers':scoped_containers(),'fault_chain_present':run('iptables','-w','-S',CHAIN,check=False).returncode==0,
            'active':json.loads(STATE.read_text()) if STATE.exists() else None}
    print(json.dumps(value))

if __name__=='__main__':main()
