"""Scoped worker5/model12 storage migration window, no old maintenance binding.

Leaves MaaS paused on error. Does not restart workers, containers or VMs itself.
The later OCR activation must own the recorded pause generation.
"""
import argparse,json,os,sys,time
from pathlib import Path
sys.path[:0]=['/root/wtaobj','/root/wtaobj/maas-framework/scripts/maas/runtime']
from gpustack_admin import api
from e5_ocr_pilot import cli
ROOT=Path('/root/wtaobj/maas-e5-storage')
STATE=ROOT/'window.private.json'


def save(d):
    with STATE.open('w') as f:json.dump(d,f,indent=2);f.flush();os.fsync(f.fileno())
    STATE.chmod(0o600)


def worker_update(old,value):
    current=api('/v2/workers/5')
    assert current['name']==old['name'] and current['labels']==old['labels']
    return api('/v2/workers/5',{'name':old['name'],'labels':old['labels'],'maintenance':value},'PUT')


def prepare():
    ROOT.mkdir(mode=0o700,exist_ok=True);assert not STATE.exists()
    w=api('/v2/workers/5');m=api('/v2/models/12')
    assert w['cluster_id']==3 and w['state']=='ready' and w['name']=='e5-9c5be5b92f10f84b714a5dbe'
    assert not (w.get('maintenance') or {}).get('enabled')
    assert m['cluster_id']==3 and m['name']=='maas-ocr-e5-r1' and m['replicas']==m['ready_replicas']==1
    assert {x['id'] for x in api('/v2/models')['items'] if x['cluster_id']==3}=={12}
    d=cli('drain-status');status=cli('status')
    assert not d['paused'] and not any(d[k] for k in ['active_tasks','backend_activity','sync_activity'])
    assert not status['tasks'].get('QUEUED',0) and not status['unknown_backend_activity'] and not status['unknown_sync_activity']
    s={'phase':'PREPARED','worker':w,'model':m,'maintenance':{'enabled':True,'message':'maas:model-storage-v1:20261026'}};save(s)
    paused=cli('pause','--reason','E5 model-storage-v1 worker container migration')
    s.update(phase='PAUSED',generation=paused['generation']);save(s)
    assert cli('drain-status')['managed_requests_drained']
    worker_update(w,s['maintenance']);s['phase']='MAINTENANCE';save(s)
    change=dict(m);change['replicas']=0;api('/v2/models/12',change,'PUT')
    s['phase']='SCALING_ZERO';save(s)
    end=time.monotonic()+90
    while api('/v2/models/12/instances')['items']:
        if time.monotonic()>end:raise RuntimeError('Replica stop not confirmed; admission remains paused')
        time.sleep(1)
    s['phase']='DRAINED';save(s)
    print(json.dumps({'phase':s['phase'],'generation':s['generation'],'worker_id':5,'old_model_id':12,'vm_rebooted':False}))


def release():
    s=json.loads(STATE.read_text());assert s['phase']=='DRAINED'
    d=cli('drain-status');assert d['paused'] and d['generation']==s['generation'] and d['managed_requests_drained']
    assert api('/v2/models/12')['replicas']==0
    # v2.2.3 reports healthy maintained workers as state=maintenance, not ready.
    w=api('/v2/workers/5');assert w['state']=='maintenance' and not w['unreachable'] and w['maintenance']==s['maintenance']
    worker_update(s['worker'],s['worker'].get('maintenance'))
    s['phase']='WORKER_RELEASED';save(s)
    end=time.monotonic()+60
    while api('/v2/workers/5')['state']!='ready':
        if time.monotonic()>end:raise RuntimeError('Worker not ready after release; admission remains paused')
        time.sleep(1)
    print(json.dumps({'phase':s['phase'],'generation':s['generation'],'admission_still_paused':True}))


if __name__=='__main__':
    import socket
    assert socket.gethostname()=='XZ-31-001' and os.getuid()==0
    p=argparse.ArgumentParser();p.add_argument('phase',choices=['prepare','release']);a=p.parse_args()
    if a.phase=='prepare':prepare()
    else:release()
