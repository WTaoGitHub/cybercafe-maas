"""Controller-side harness for authorized offline tests of the EXISTING worker 002."""
import argparse,base64,json,os,re,secrets,socket,subprocess,sys,time,urllib.request,urllib.error
from pathlib import Path
ROOT=Path('/root/wtaobj/maas-framework');WORK=Path('/root/wtaobj/maas-offline-controller');PRIVATE=ROOT/'.secrets'/'offline002'
sys.path.insert(0,str(ROOT));sys.path.insert(0,str(ROOT/'scripts/maas/p0'))
from common import Resources,admin
from maas_platform.versioning import VersionPin,register_pin,verify_pin
from maas_platform.collector import atomic_json
DATABASE='maas_offline_test';API='wta-maas-offline-api';EXEC='wta-maas-offline-executor'


def run(*args):return subprocess.check_output(args,text=True,stderr=subprocess.PIPE,timeout=45)
def state():return json.loads((WORK/'state.json').read_text())
def save(s):atomic_json(WORK/'state.json',s)
def write(path,value):
    fd=os.open(path,os.O_CREAT|os.O_TRUNC|os.O_WRONLY,0o600)
    with os.fdopen(fd,'w') as f:f.write(value)
def wait(fn,timeout=140):
    end=time.monotonic()+timeout
    while time.monotonic()<end:
        value=fn()
        if value:return value
        time.sleep(.3)
    raise RuntimeError('Test readiness deadline reached')
def request(path,body=None,headers=None):
    key=(PRIVATE/'key').read_text().strip()
    if isinstance(body,dict):body=json.dumps(body).encode()
    h={'Authorization':'Bearer '+key}
    if body is not None:h['Content-Type']='application/json'
    with urllib.request.urlopen(urllib.request.Request('http://127.0.0.1:18830'+path,data=body,headers={**h,**(headers or {})}),timeout=15) as r:
        return r.status,json.load(r)
def docker_args():
    return ['docker','run','--rm','--network','host','--env-file',str(PRIVATE/'app.env'),'-v',str(ROOT)+':/work',
        '-v',str(WORK)+':/trusted:ro','-w','/work','python:3.11-slim']
def sql(query):return run('docker','exec','wta-maas-p1-test-db','psql','-U','maas','-d',DATABASE,'-tAc',query).strip()
def task_state(ident):
    assert re.fullmatch(r'task_[a-f0-9]{32}',ident)
    return json.loads(sql("SELECT json_build_object('id',id,'state',state,'error_code',error_code,'attempts',attempts,'result_artifact_id',result_artifact_id) FROM tasks WHERE id='"+ident+"'"))


def init():
    if (WORK/'state.json').exists():raise RuntimeError('Prior test state exists; inspect before reuse')
    probe=socket.socket();probe.bind(('127.0.0.1',18830));probe.close()
    WORK.mkdir(mode=0o700,exist_ok=True);PRIVATE.mkdir(mode=0o700,exist_ok=True)
    res=Resources();s={'resources':[],'tasks':{}}
    try:
        backend=json.loads(Path('/root/wtaobj/maas-runtime/target.json').read_text())['backend']
        b=admin('/v2/inference-backends/'+str(backend))
        model=res.create('/v2/models',{'name':'maas-ocr-002-offline','source':'local_path','local_path':'/root/wtaobj/gpu-ocr',
            'cluster_id':1,'replicas':1,'categories':['llm'],'worker_selector':{'worker-name':'XZ-31-002'},
            'backend':b['backend_name'],'backend_version':'p2-r2','cpu_offloading':False,
            'distributed_inference_across_workers':False,'env':{'GPUSTACK_MODEL_VRAM_CLAIM':str(2*1024**3)}})
        route=next((x for x in admin('/v2/model-routes')['items'] if x['name']==model['name']),None)
        if route is None:route=res.create('/v2/model-routes',{'name':model['name'],'categories':['llm'],'access_policy':'authed','targets':[{'model_id':model['id'],'weight':100}]})
        else:res.created.append(('/v2/model-routes',route['id']))
        s.update(model=model['id'],route=route['id'],backend=backend,name=model['name'])
        def ready():
            rows=admin('/v2/models/'+str(model['id'])+'/instances')['items']
            if len(rows)==1 and rows[0]['state']=='running':
                assert rows[0]['worker_name']=='XZ-31-002'
                return rows
        wait(ready)
        run('docker','exec','wta-maas-p1-test-db','psql','-U','maas','-d','postgres','-c','CREATE DATABASE '+DATABASE)
        _,op=res.user('002-offline');_,key=res.key(op,'002-offline',[model['name'],'rapidocr-gpu'],expires=3600)
        write(PRIVATE/'key',key);write(PRIVATE/'pepper',secrets.token_hex(32))
        write(PRIVATE/'bindings.json',json.dumps({'credentials':[{'id':'offline002','project':'offline002','secret_file':'/work/.secrets/offline002/key','models':['ocr.default','mock.echo']}]}))
        write(PRIVATE/'catalog.json',json.dumps({'ocr.default':{'driver':'gpustack.ocr','revision':'ocr-002-offline-r1',
            'upstream_model':model['name'],'max_runtime_s':8,'pin_file':'/trusted/pin.json','preflight_file':'/trusted/preflight.json'},
            'mock.echo':{'driver':'mock','revision':'1','max_runtime_s':10}}))
        url=(ROOT/'.secrets/test.env').read_text().strip().split('=',1)[1].replace('/maas_test','/'+DATABASE)
        write(PRIVATE/'app.env','\n'.join(['MAAS_DATABASE_URL='+url,'MAAS_ARTIFACT_ROOT=/work/offline002-artifacts',
            'MAAS_GPUSTACK_URL=http://127.0.0.1:18080','MAAS_BINDINGS_FILE=/work/.secrets/offline002/bindings.json',
            'MAAS_CATALOG_FILE=/work/.secrets/offline002/catalog.json','MAAS_PEPPER_FILE=/work/.secrets/offline002/pepper',
            'MAAS_LEASE_SECONDS=8','MAAS_HEARTBEAT_SECONDS=1'])+'\n')
        args=docker_args();run(*args[:-1],'-d','--name',API,args[-1],'/work/.venv/bin/uvicorn','maas_platform.api:create_app',
            '--factory','--host','127.0.0.1','--port','18830')
        for _ in range(100):
            try:
                if request('/healthz')[0]==200:break
            except Exception:pass
            time.sleep(.2)
        else:raise RuntimeError('Temporary API not ready')
        image=Path('/root/wtaobj/service-validation/fixtures/upright.png').read_bytes()
        code,art=request('/maas/v1/artifacts',image,{'Content-Type':'image/png'});assert code==201
        s['input']=art['id'];s['resources']=res.created;save(s)
        return {'initialized':True,'model':s['model'],'worker_selector':{'worker-name':'XZ-31-002'}}
    except BaseException:
        res.cleanup();subprocess.run(['docker','rm','-f',API],capture_output=True)
        for p in PRIVATE.glob('*'):p.unlink()
        raise


def snapshot(inventory):
    s=state();model=admin('/v2/models/'+str(s['model']));assert model['worker_selector']=={'worker-name':'XZ-31-002'}
    worker=next(x for x in admin('/v2/workers')['items'] if x['name']=='XZ-31-002')
    assert worker['state']=='ready' and not worker.get('unreachable',False)
    rows=admin('/v2/models/'+str(s['model'])+'/instances')['items']
    assert len(rows)==1 and rows[0]['worker_name']=='XZ-31-002' and rows[0]['state']=='running'
    assert inventory['worker']=='XZ-31-002' and inventory['running_image_ids']==[inventory['image_id']] and not inventory['paused']
    route=admin('/v2/model-routes/'+str(s['route']));targets=admin('/v2/model-route-targets?route_id='+str(s['route']))['items'];backend=admin('/v2/inference-backends/'+str(s['backend']))
    with urllib.request.urlopen('http://127.0.0.1:18080/version',timeout=5) as r:version=json.load(r)
    code=(ROOT/'scripts/maas/p0/inspect_gateway.py').read_text()
    gateway=json.loads(run('docker','exec','wta-gpustack-server','python','-c',code))
    policy=next(x['policies'] for x in gateway['ingresses'] if x['name']=='ai-route-route-'+str(s['route'])+'.internal')
    assert policy['higress.io/proxy-next-upstream-tries']=='2'
    inv={'XZ-31-002':inventory};pinpath=WORK/'pin.json'
    if pinpath.exists():pin=VersionPin(**json.loads(pinpath.read_text()))
    else:
        pin=register_pin('ocr-002-offline-r1',route,targets,model,backend,inventory['image_id'],['XZ-31-002'])
        atomic_json(pinpath,pin.public())
    verify_pin(pin,version,route,targets,model,backend,inv)
    atomic_json(WORK/'preflight.json',{'observed_at':time.time(),'version':version,'route':route,'targets':targets,'model':model,
        'backend':backend,'inventory':inv,'gateway_max_attempts':3})
    return {'snapshot_valid':True,'worker':'XZ-31-002','image_id':inventory['image_id']}


def submit(label,mock=False):
    s=state();body={'model':'mock.echo','input':{'text':label}} if mock else {'model':'ocr.default','input':{'image':{'artifact_id':s['input']}},'timeout_seconds':180}
    code,row=request('/maas/v1/tasks',body,{'Idempotency-Key':label});assert code==202
    s['tasks'][label]=row['id'];save(s);return row

def execute(label):
    args=docker_args()
    code='from maas_platform.config import Settings;from maas_platform.auth import AuthConnector;from maas_platform.service import TaskService;from maas_platform.worker import Worker;s=Settings.from_env();v=TaskService(s,AuthConnector(s));v.db.migrate();print(Worker(v).run_once())'
    run(*args[:-1],'--name',EXEC,args[-1],'/work/.venv/bin/python','-c',code)
    return status(label)

def status(label=None):
    s=state();result={'worker':next({k:w.get(k) for k in ('id','name','state','unreachable')} for w in admin('/v2/workers')['items'] if w['name']=='XZ-31-002'),
        'collector':json.loads(Path('/root/wtaobj/maas-runtime/health.json').read_text()),
        'test_instances':[{k:x.get(k) for k in ('id','name','state','worker_name','port')} for x in admin('/v2/models/'+str(s['model'])+'/instances')['items']]}
    if label:
        row=task_state(s['tasks'][label]);result['task']=row
        result['backend_activity']=json.loads(sql("SELECT coalesce(json_agg(json_build_object('token',token,'state',state)), '[]'::json) FROM backend_activity WHERE task_id='"+row['id']+"'"))
    return result

def result(label):
    s=state();row=task_state(s['tasks'][label]);assert row['state']=='SUCCEEDED'
    code,value=request('/maas/v1/artifacts/'+row['result_artifact_id']);assert code==200
    return {'download_status':code,'lines':len(value['outputs'].get('lines',[])),'revision':value['model_revision']}

def scale(replicas):
    s=state();m=admin('/v2/models/'+str(s['model']));m['replicas']=replicas;admin('/v2/models/'+str(s['model']),m,'PUT')
    return {'replicas':replicas}

def resolve(label):
    s=state();ident=s['tasks'][label]
    token=sql("SELECT token FROM backend_activity WHERE task_id='"+ident+"' AND state='UNKNOWN'")
    assert re.fullmatch(r'[a-f0-9]{32}',token)
    m=admin('/v2/models/'+str(s['model']));assert m['replicas']==0
    args=docker_args();code='from maas_platform.config import Settings;from maas_platform.auth import AuthConnector;from maas_platform.service import TaskService;s=Settings.from_env();v=TaskService(s,AuthConnector(s));assert v.resolve_backend('+repr(token)+',"Existing 002 worker stopped, test inference processes removed and desired replicas set to zero")'
    run(*args,'/work/.venv/bin/python','-c',code)
    return {'resolved':True}

def probe():
    s=state();key=(PRIVATE/'key').read_text().strip()
    try:
        with socket.create_connection(('10.11.31.2',10150),timeout=2):reachable=True
    except OSError:reachable=False
    image=Path('/root/wtaobj/service-validation/fixtures/upright.png').read_bytes()
    payload={'model':'rapidocr-gpu','messages':[{'role':'user','content':[{'type':'image_url','image_url':{'url':'data:image/png;base64,'+base64.b64encode(image).decode()}}]}]}
    try:
        req=urllib.request.Request('http://127.0.0.1:18080/v1/chat/completions',data=json.dumps(payload).encode(),headers={'Content-Type':'application/json','Authorization':'Bearer '+key})
        with urllib.request.urlopen(req,timeout=10) as response:
            value=json.loads(json.load(response)['choices'][0]['message']['content'])
            sync={'status':response.status,'lines':len(value['lines'])}
    except urllib.error.HTTPError as exc:sync={'status':exc.code}
    except OSError:sync={'status':'transport_unavailable'}
    code=(ROOT/'scripts/maas/p0/inspect_gateway.py').read_text()
    routes=json.loads(run('docker','exec','wta-gpustack-server','python','-c',code))['ingresses']
    return {'worker_port_reachable':reachable,'original_sync_ocr':sync,
        'test_route':next((x for x in routes if x['name']=='ai-route-route-'+str(s['route'])+'.internal'),None)}


def cleanup():
    s=state()
    evidence={'tasks':json.loads(sql("SELECT coalesce(json_agg(json_build_object('id',id,'state',state,'attempts',attempts,'error_code',error_code)), '[]'::json) FROM tasks")),
        'successful_events':int(sql('SELECT count(*) FROM task_usage_events')),'unknown_activities':int(sql("SELECT count(*) FROM backend_activity WHERE state='UNKNOWN'"))}
    for name in (EXEC,API):subprocess.run(['docker','rm','-f',name],capture_output=True)
    res=Resources();res.created=s['resources'];evidence['resources_removed']=res.cleanup();evidence['cleanup_failures']=res.failures
    for p in PRIVATE.glob('*'):p.unlink()
    run('docker','exec','wta-maas-p1-test-db','psql','-U','maas','-d','postgres','-c','DROP DATABASE '+DATABASE)
    import shutil
    shutil.rmtree(ROOT/'offline002-artifacts',ignore_errors=True)
    atomic_json(WORK/'cleanup.json',evidence)
    return evidence


def main():
    p=argparse.ArgumentParser();p.add_argument('operation',choices=['init','snapshot','submit','mock','execute','status','result','scale0','scale1','resolve','probe','cleanup']);p.add_argument('--label');a=p.parse_args()
    if a.operation=='init':value=init()
    elif a.operation=='snapshot':value=snapshot(json.load(sys.stdin))
    elif a.operation in ('submit','mock'):value=submit(a.label,a.operation=='mock')
    elif a.operation=='execute':value=execute(a.label)
    elif a.operation=='status':value=status(a.label)
    elif a.operation=='result':value=result(a.label)
    elif a.operation=='scale0':value=scale(0)
    elif a.operation=='scale1':value=scale(1)
    elif a.operation=='resolve':value=resolve(a.label)
    elif a.operation=='probe':value=probe()
    else:value=cleanup()
    print(json.dumps(value,default=str))

if __name__=='__main__':main()
