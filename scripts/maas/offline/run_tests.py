"""Local orchestrator. Existing node 002 only; management SSH and rollback timers retained."""
import os
import argparse,json,shlex,subprocess,time
from pathlib import Path
REPO=Path(__file__).resolve().parents[3]
FIRST=['ssh','-S',str(REPO/'service-validation/ssh-001.sock'),'-o','ProxyCommand=false','root@10.11.31.1']
SECOND=['ssh','-F',os.environ.get('MAAS_SSH_CONFIG',str(Path(__file__).resolve().parents[2] / '.maas/ssh/config')),'-o','BatchMode=yes',os.environ.get('MAAS_LEGACY_WORKER_SSH_ALIAS','maas-legacy-worker')]
BASE='/root/wtaobj/maas-framework/scripts/maas/offline/'
OUTPUT=REPO/'results/maas-worker-offline';OUTPUT.mkdir(parents=True,exist_ok=True)


def command(host,script,operation,*args):return host+[shlex.join(['python3',BASE+script,operation,*args])]
def call(host,script,operation,*args,input=None):
    result=subprocess.run(command(host,script,operation,*args),input=input,text=True,capture_output=True,timeout=160)
    if result.returncode:raise RuntimeError(script+' '+operation+' failed: '+result.stderr[-1500:])
    return json.loads(result.stdout)
def control(operation,*args,**kw):return call(FIRST,'controller.py',operation,*args,**kw)
def node(operation,*args):return call(SECOND,'node002.py',operation,*args)
def refresh():return control('snapshot',input=json.dumps(node('inventory')))
def wait_status(condition,timeout=140,label=None):
    end=time.monotonic()+timeout
    while time.monotonic()<end:
        value=control('status',*(['--label',label] if label else []))
        if condition(value):return value
        time.sleep(1)
    raise RuntimeError('Worker state did not converge')
def ready():return wait_status(lambda v:v['worker']['state']=='ready' and v['collector']['ok'] and len(v['test_instances'])==1 and v['test_instances'][0]['state']=='running')
def save(name,value):
    (OUTPUT/(name+'.json')).write_text(json.dumps(value,indent=2)+'\n')


def idle():
    result={'phase':'idle-worker-stop','target':'XZ-31-002','existing_worker':True,'new_workers_created':False}
    try:
        result['baseline']=ready();refresh()
        control('submit','--label','baseline');result['baseline_task']=control('execute','--label','baseline')
        assert result['baseline_task']['task']['state']=='SUCCEEDED'
        result['baseline_result']=control('result','--label','baseline')
        result['before_probe']=control('probe')
        result['armed']=node('arm','--action','stop');started=time.monotonic()
        result['fault']=node('apply')
        result['offline']=wait_status(lambda v:v['worker']['state']!='ready',110)
        result['observed_offline_after_s']=round(time.monotonic()-started,2)
        result['offline_probe']=control('probe')
        result['node_after_stop']=node('status')
        result['stored_result_while_offline']=control('result','--label','baseline')
        assert not result['fault']['worker']['running']
        assert result['stored_result_while_offline']['lines']>0
        result['restored']=node('restore');result['recovered']=ready();refresh()
        control('submit','--label','after-idle-stop');row=control('execute','--label','after-idle-stop')
        assert row['task']['state']=='SUCCEEDED'
        result['recovery_task']=row;result['recovery_result']=control('result','--label','after-idle-stop')
        result['ok']=True
    finally:
        result['final_node_restore']=node('restore');save('idle',result)
    return result


def inflight(action):
    label='inflight-'+action
    result={'phase':label,'target':'XZ-31-002','existing_worker':True,'management_ssh_preserved':True}
    process=None
    try:
        result['baseline']=ready();result['snapshot']=refresh()
        result['armed']=node('arm','--action',action,'--pause')
        control('submit','--label',label)
        process=subprocess.Popen(command(FIRST,'controller.py','execute','--label',label),stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
        result['dispatch_observed']=wait_status(lambda v:any(x['state']=='DISPATCHING' for x in v['backend_activity']),15,label)
        started=time.monotonic();result['fault']=node('apply')
        stdout,stderr=process.communicate(timeout=35)
        if process.returncode:raise RuntimeError('In-flight executor failed: '+stderr[-1200:])
        result['outcome']=json.loads(stdout);row=result['outcome']['task']
        assert row['state']=='FAILED' and row['error_code']=='UNKNOWN_BACKEND_ACTIVITY' and row['attempts']==1,row
        assert result['outcome']['backend_activity'][0]['state']=='UNKNOWN'
        result['offline']=wait_status(lambda v:v['worker']['state']!='ready',110)
        result['offline_after_s']=round(time.monotonic()-started,2)
        result['node_offline']=node('status');result['offline_probe']=control('probe')
        if action=='partition':
            assert result['node_offline']['fault_chain_present']
            assert not result['offline_probe']['worker_port_reachable']
        result['stored_result']=control('result','--label','baseline')
        assert result['stored_result']['lines']==46
        pending='pending-'+action;control('mock','--label',pending)
        result['blocked']=control('execute','--label',pending)
        assert result['blocked']['task']['state']=='QUEUED'
        # Stop the actual backend and prevent local respawn before releasing UNKNOWN.
        control('scale0');result['termination_evidence']=node('terminate-test-backend')
        assert not result['termination_evidence']['remaining_test_backends'] and result['termination_evidence']['worker_stopped']
        result['resolution']=control('resolve','--label',label)
        result['resumed_pending']=control('execute','--label',pending)
        assert result['resumed_pending']['task']['state']=='SUCCEEDED'
        result['restored']=node('restore');control('scale1');result['recovered']=ready();refresh()
        after='after-'+action;control('submit','--label',after);result['recovery_task']=control('execute','--label',after)
        assert result['recovery_task']['task']['state']=='SUCCEEDED'
        result['recovery_result']=control('result','--label',after)
        assert result['recovery_result']['lines']==46
        result['ok']=True
    finally:
        if process is not None and process.poll() is None:
            process.terminate();process.wait(timeout=10)
        result['final_node_restore']=node('restore')
        control('scale1')
        save(action,result)
    return result


def main():
    p=argparse.ArgumentParser();p.add_argument('phase',choices=['idle','kill','partition']);a=p.parse_args()
    value=idle() if a.phase=='idle' else inflight(a.phase)
    print(json.dumps({'phase':value['phase'],'ok':value.get('ok',False),
                     'offline_after_s':value.get('observed_offline_after_s',value.get('offline_after_s'))}))

if __name__=='__main__':main()
