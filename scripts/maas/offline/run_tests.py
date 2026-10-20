"""Local orchestrator. Existing node 002 only; management SSH and rollback timers retained."""
import argparse,json,shlex,subprocess,time
from pathlib import Path
REPO=Path(__file__).resolve().parents[3]
FIRST=['ssh','-S',str(REPO/'service-validation/ssh-001.sock'),'-o','ProxyCommand=false','root@10.11.31.1']
SECOND=['ssh','-p','16721','-i',str(Path.home()/'.ssh/id_ed25519'),'-o','BatchMode=yes','root@111.4.255.126']
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


def main():
    p=argparse.ArgumentParser();p.add_argument('phase',choices=['idle']);a=p.parse_args()
    value=idle();print(json.dumps({'phase':value['phase'],'ok':value.get('ok',False),'offline_after_s':value['observed_offline_after_s']}))

if __name__=='__main__':main()
