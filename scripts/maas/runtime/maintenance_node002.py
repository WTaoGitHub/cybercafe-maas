"""Private actuator on existing 002. No VM shutdown, new worker, daemon or model deletion.

Only stop after the controller scales the entire cluster to zero and Docker
reports no remaining GPU workloads. Independent rollback restarts original worker.
"""
import argparse,json,os,socket,subprocess,time,uuid
from pathlib import Path
from inspect_stop import inspect
ROOT=Path('/root/wtaobj/maas-node002-maintenance');STATE=ROOT/'state.json'
WORKER='wta-gpustack-worker';AGENT='cybercafe-agent.service'


def run(args,check=True):return subprocess.run(args,check=check,text=True,capture_output=True,timeout=25)
def worker():return json.loads(run(['docker','inspect',WORKER]).stdout)[0]
def write(state):
    ROOT.mkdir(mode=0o700,exist_ok=True);temp=STATE.with_suffix('.tmp')
    fd=os.open(temp,os.O_WRONLY|os.O_CREAT|os.O_TRUNC,0o600)
    with os.fdopen(fd,'w') as f:json.dump(state,f);f.flush();os.fsync(f.fileno())
    os.replace(temp,STATE)
def guard():
    if socket.gethostname()!='XZ-31-002' or '10.11.31.2' not in run(['hostname','-I']).stdout.split():raise RuntimeError('Existing worker 002 only')


def arm():
    guard()
    if STATE.exists() and json.loads(STATE.read_text()).get('active'):raise RuntimeError('Existing maintenance rollback is armed')
    for name in ('cybercafe-h3','cloudflared'):
        c=json.loads(run(['docker','inspect',name]).stdout)[0]
        if c['State']['Running'] or c['HostConfig']['RestartPolicy']['Name']!='no':raise RuntimeError('H3/tunnel must remain parked')
    w=worker()
    if not w['State']['Running']:raise RuntimeError('Worker must be running at baseline')
    state={'active':True,'worker_id':w['Id'],'restart':w['HostConfig']['RestartPolicy']['Name'],
        'agent_active':run(['systemctl','is-active',AGENT],False).returncode==0,
        'timer':'maas-node002-restore-'+uuid.uuid4().hex[:10],'armed_at':time.time()}
    write(state)
    run(['systemd-run','--quiet','--unit',state['timer'],'--on-active=600s','/usr/bin/python3',str(Path(__file__).resolve()),'restore'])
    return {'armed':True,'worker_id':w['Id'],'rollback_after_s':600}


def stop():
    guard();state=json.loads(STATE.read_text())
    if not state['active'] or worker()['Id']!=state['worker_id']:raise RuntimeError('Worker identity or rollback state changed')
    # No background management command may restart H3 while inspecting this node.
    if state['agent_active']:run(['systemctl','stop',AGENT])
    before=inspect()
    if set(before['running_workload_containers'])-{WORKER}:raise RuntimeError('GPU workloads remain; wait for replica cleanup instead of force-deleting them')
    run(['docker','stop','-t','10',WORKER]);run(['docker','update','--restart=no',WORKER])
    proof=inspect();end=time.monotonic()+10
    while not proof['backend_stop_observed'] and time.monotonic()<end:
        time.sleep(1);proof=inspect()
    if not proof['backend_stop_observed']:raise RuntimeError('GPU activity is not empty; worker stopped but node stop is unconfirmed')
    proof.update(worker_id=state['worker_id'],legacy_agent_active=run(['systemctl','is-active',AGENT],False).returncode==0,
                 node_management_ssh_preserved=True,vm_powered_off=False)
    write({**state,'stopped_at':time.time(),'stop_proof':proof})
    return proof


def restore():
    guard()
    if not STATE.exists():return {'restored':True,'no_prior_state':True}
    state=json.loads(STATE.read_text())
    if not state['active']:return {'restored':True,'inactive_session':True}
    if worker()['Id']!=state['worker_id']:raise RuntimeError('Refuse to replace an existing worker')
    if not worker()['State']['Running']:
        run(['docker','update','--restart='+state['restart'],WORKER]);run(['docker','start',WORKER])
    if state['agent_active']:run(['systemctl','start',AGENT])
    state['restored_at']=time.time();write(state)
    # Keep rollback armed until controller replica restoration/probes pass.
    return {'restored':True,'worker_id':worker()['Id'],'running':worker()['State']['Running']}


def disarm():
    guard();state=json.loads(STATE.read_text())
    if not worker()['State']['Running']:raise RuntimeError('Cannot disarm recovery while worker is stopped')
    run(['systemctl','stop',state['timer']+'.timer'],False);state['active']=False;write(state)
    return {'disarmed':True}


def main():
    p=argparse.ArgumentParser();p.add_argument('action',choices=['arm','stop','restore','disarm','status','ssh']);a=p.parse_args();guard()
    if a.action=='ssh':
        a.action=os.environ.get('SSH_ORIGINAL_COMMAND','')
        if a.action not in ('arm','stop','restore','disarm','status'):raise SystemExit('Unsupported maintenance action')
    value=globals()[a.action]() if a.action!='status' else inspect()
    print(json.dumps(value))


if __name__=='__main__':main()
