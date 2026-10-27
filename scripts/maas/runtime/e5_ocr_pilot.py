"""Controller-side E5 OCR pilot: create an isolated target, then switch idle MaaS.

Uses the existing private admin helper. No credentials are printed. No VM,
Docker engine, collector or existing model is restarted. Run phases explicitly;
inspect private state after any uncertain control-plane response before retrying.
"""
import argparse,json,os,sys,time
from pathlib import Path
ROOT=Path('/root/wtaobj/maas-framework')
sys.path[:0]=[str(ROOT),'/root/wtaobj']
from gpustack_admin import api
from maas_platform.versioning import register_pin

LIVE=Path('/root/wtaobj/maas-live')
WORK=Path('/root/wtaobj/maas-e5-ocr')
STATE=WORK/'target.json'
NAME='maas-ocr-e5-r1'
REVISION='ocr-e5-r1'
IMAGE='quay.io/cybercafe-maas-ocr:p2-r1'
IMAGE_ID='sha256:8eed873554282ec02ba4d8d3a78f8230178cef2ee4f6f94ca6dfb858336e89e5'
MODEL_PATH='/srv/maas/models/image-contained/model'


def save(path,data):
    raw=(json.dumps(data,indent=2)+'\n').encode()
    fd=os.open(path,os.O_CREAT|os.O_TRUNC|os.O_WRONLY,0o600)
    with os.fdopen(fd,'wb') as f:f.write(raw);f.flush();os.fsync(f.fileno())


def cli(*args):
    import subprocess
    command=['docker','compose','--env-file',str(LIVE/'app.env'),'-f',str(ROOT/'deploy/maas/compose.yaml'),
             'exec','-T','api','python','-m','maas_platform.cli',*args]
    p=subprocess.run(command,capture_output=True,text=True,timeout=90)
    if p.returncode:raise RuntimeError('MaaS operator command failed; inspect service state')
    return json.loads(p.stdout)


def create(worker_id):
    if STATE.exists():raise RuntimeError('Target state exists; inspect before retrying creation')
    worker=api('/v2/workers/'+str(worker_id))
    assert worker['cluster_id']==3 and worker['state']=='ready'
    assert not any(m['name']==NAME for m in api('/v2/models')['items'])
    assert not any(r['name']==NAME for r in api('/v2/model-routes')['items'])
    backend=next(b for b in api('/v2/inference-backends')['items'] if b['backend_name']=='MaaS-OCR-P2-R2-custom')
    assert backend['version_configs']['p2-r2']['image_name']==IMAGE
    state={'phase':'CREATING','worker_id':worker_id,'worker_name':worker['name'],'cluster_id':3,'backend_id':backend['id']}
    save(STATE,state)
    model=api('/v2/models',{'name':NAME,'source':'local_path','local_path':MODEL_PATH,
        'cluster_id':3,'replicas':1,'categories':['llm'],'placement_strategy':'spread',
        'backend':backend['backend_name'],'backend_version':'p2-r2','cpu_offloading':False,
        'distributed_inference_across_workers':False,'worker_selector':{'worker-name':worker['name']},
        'env':{'GPUSTACK_MODEL_VRAM_CLAIM':str(2*1024**3)}})
    state.update(model_id=model['id'],phase='MODEL_CREATED');save(STATE,state)
    route=next((r for r in api('/v2/model-routes')['items'] if r['name']==NAME),None)
    if route is None:
        route=api('/v2/model-routes',{'name':NAME,'categories':['llm'],'access_policy':'authed',
                                     'targets':[{'model_id':model['id'],'weight':100}]})
    assert route['access_policy']=='authed'
    state.update(route_id=route['id'],phase='CREATED');save(STATE,state)
    print(json.dumps(state))


def activate(paused_generation=None):
    state=json.loads(STATE.read_text());assert state['phase']=='CREATED'
    model=api('/v2/models/'+str(state['model_id']))
    assert model['cluster_id']==3 and model['ready_replicas']==1
    instances=api('/v2/models/'+str(model['id'])+'/instances')['items']
    assert len(instances)==1 and instances[0]['worker_id']==state['worker_id'] and instances[0]['state']=='running'
    route=api('/v2/model-routes/'+str(state['route_id']))
    targets=api('/v2/model-route-targets?route_id='+str(route['id']))['items']
    backend=api('/v2/inference-backends/'+str(state['backend_id']))
    pin=register_pin(REVISION,route,targets,model,backend,IMAGE_ID,[state['worker_name']])
    save(WORK/'pin.json',pin.public())
    status=cli('status');drain=cli('drain-status')
    assert not any(drain[k] for k in ['active_tasks','backend_activity','sync_activity'])
    if paused_generation is None:assert not drain['paused']
    else:assert drain['paused'] and drain['generation']==paused_generation
    assert not status['tasks'].get('QUEUED',0) and not status['unknown_backend_activity'] and not status['unknown_sync_activity']
    identity=json.loads((LIVE/'identity.json').read_text())
    keypath='/v2/api-keys/'+str(identity['key_id'])
    # v2.2.3 exposes list/update, not GET-by-ID, for API keys.
    keys=api('/v2/api-keys?user_id='+str(identity['user_id'])+'&perPage=100')['items']
    key=next(k for k in keys if k['id']==identity['key_id'])
    backup={'catalog':json.loads((LIVE/'catalog.json').read_text()),'key_id':identity['key_id'],
            'allowed_model_names':key.get('allowed_model_names'),'created_at':time.time()}
    save(WORK/'switch-backup.json',backup)
    if paused_generation is None:cli('pause','--reason','E5 new-clone OCR route activation')
    assert cli('drain-status')['managed_requests_drained']
    allowed=key.get('allowed_model_names')
    # Empty/None means unrestricted; do not accidentally narrow a shared key.
    if allowed:api(keypath,{'allowed_model_names':sorted(set(allowed+[NAME])),'scope':key['scope']},'PUT')
    if paused_generation is not None:
        check=cli('drain-status');assert check['paused'] and check['generation']==paused_generation
    state['phase']='SWITCHING';save(STATE,state)
    catalog={'ocr.default':{'driver':'gpustack.ocr','revision':REVISION,'upstream_model':NAME,'max_runtime_s':25}}
    # Existing consumers bind-mount this file's inode; write only while drained/paused.
    # Preserve its permissions and ownership, and leave admission paused on failure.
    with (LIVE/'catalog.json').open('w') as f:json.dump(catalog,f);f.flush();os.fsync(f.fileno())
    result=cli('resume','--credential-id','pilot-ocr','--probe-image','/run/maas-probe/upright.png')
    assert not result['maintenance']['paused']
    state.update(phase='ACTIVE',revision=REVISION,resume_probes=result['probes']);save(STATE,state)
    print(json.dumps(state))


if __name__=='__main__':
    import socket
    assert socket.gethostname()=='XZ-31-001' and os.getuid()==0
    WORK.mkdir(mode=0o700,exist_ok=True)
    parser=argparse.ArgumentParser();parser.add_argument('phase',choices=['create','activate']);parser.add_argument('--worker-id',type=int,default=5)
    parser.add_argument('--name',default=NAME);parser.add_argument('--revision',default=REVISION)
    parser.add_argument('--work-dir',default=str(WORK));parser.add_argument('--model-path',default=MODEL_PATH)
    parser.add_argument('--paused-generation',type=int)
    parser.add_argument('--image-name',default=IMAGE);parser.add_argument('--image-id',default=IMAGE_ID)
    args=parser.parse_args()
    NAME=args.name;REVISION=args.revision;MODEL_PATH=args.model_path;WORK=Path(args.work_dir);STATE=WORK/'target.json'
    IMAGE=args.image_name;IMAGE_ID=args.image_id
    WORK.mkdir(mode=0o700,parents=True,exist_ok=True)
    if args.phase=='create':create(args.worker_id)
    else:activate(args.paused_generation)
