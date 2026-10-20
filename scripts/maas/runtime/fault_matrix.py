"""Real task-chain faults against retained pilot only; temporary tenant, API and DB.
Original GPUStack workers and original OCR containers are never stopped.
"""
import base64,hashlib,json,os,re,secrets,subprocess,sys,time,urllib.request,urllib.error
from pathlib import Path
sys.path.insert(0,'/root/wtaobj/maas-framework/scripts/maas/p0')
from common import Resources,admin
root=Path('/root/wtaobj/maas-framework');runtime=Path('/root/wtaobj/maas-runtime')
ids=json.loads((runtime/'target.json').read_text());res=Resources()
private=root/'.secrets'/'p2r2';private.mkdir(mode=0o700,exist_ok=True)
smoke_only='--smoke-only' in sys.argv
database='maas_runtime_smoke_test' if smoke_only else 'maas_p2r2_test'
class SmokeComplete(Exception):pass
containers=['wta-maas-p2r2-api','wta-maas-p2r2-executor'];report={'checks':{},'original_workers_stopped':False}
for name in containers:
    if subprocess.run(['docker','inspect',name],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL).returncode==0:raise RuntimeError('Acceptance container already exists')
def run(*args):return subprocess.check_output(args,text=True)
def write(name,data):
    p=private/name;fd=os.open(p,os.O_CREAT|os.O_TRUNC|os.O_WRONLY,0o600)
    with os.fdopen(fd,'w') as f:f.write(data)
def request(path,body=None,key=None,headers=None):
    if isinstance(body,dict):body=json.dumps(body).encode()
    h={'Authorization':'Bearer '+key} if key else {}
    if body is not None:h['Content-Type']='application/json'
    try:
        with urllib.request.urlopen(urllib.request.Request('http://127.0.0.1:18830'+path,data=body,headers={**h,**(headers or {})}),timeout=15) as r:
            return r.status,json.load(r)
    except urllib.error.HTTPError as e:return e.code,json.load(e)
def wait(check,timeout=100):
    until=time.monotonic()+timeout
    while time.monotonic()<until:
        try:
            value=check()
            if value:return value
        except (OSError,ValueError,KeyError):pass
        time.sleep(.2)
    raise RuntimeError('Fault condition did not converge')
def healthy():
    value=json.loads((runtime/'health.json').read_text())
    return value['ok'] and time.time()-value['updated_at']<15

def docker_base():
    return ['docker','run','--rm','--network','host','--env-file',str(private/'app.env'),
            '-v',str(root)+':/work','-v',str(runtime)+':/trusted:ro','-w','/work','python:3.11-slim']
def execute():
    code='from maas_platform.config import Settings;from maas_platform.auth import AuthConnector;from maas_platform.service import TaskService;from maas_platform.worker import Worker;s=Settings.from_env();v=TaskService(s,AuthConnector(s));v.db.migrate();assert Worker(v).run_once()'
    run(*docker_base()[:-1],'--name',containers[1],docker_base()[-1],'/work/.venv/bin/python','-c',code)
def sql(query):return run('docker','exec','wta-maas-p1-test-db','psql','-U','maas','-d',database,'-tAc',query).strip()
def state(task):
    assert re.fullmatch(r'task_[a-f0-9]{32}',task)
    return json.loads(sql("SELECT json_build_object('state',state,'error_code',error_code,'attempts',attempts,'result_artifact_id',result_artifact_id) FROM tasks WHERE id='"+task+"'"))
def submit(key,label):
    code,row=request('/maas/v1/tasks',payload,key,{'Idempotency-Key':label});assert code==202,(code,row)
    return row['id']
def assert_no_dispatch(ident,error):
    row=state(ident);assert row['state']=='FAILED' and row['error_code']==error,row
    assert sql("SELECT count(*) FROM backend_activity WHERE task_id='"+ident+"'")=='0'
    return {'state':row['state'],'error_code':error,'backend_dispatches':0}
original_env=None
try:
    wait(healthy)
    run('docker','exec','wta-maas-p1-test-db','psql','-U','maas','-d','postgres','-c','CREATE DATABASE '+database)
    user,op=res.user('r2-matrix')
    keyinfo,key=res.key(op,'r2-matrix',[ids['name'],'rapidocr-gpu'],expires=3600)
    write('key',key);write('pepper',secrets.token_hex(32))
    bindings={'credentials':[{'id':'matrix','project':'matrix','secret_file':'/work/.secrets/p2r2/key','models':['ocr.default']}]}
    write('bindings.json',json.dumps(bindings))
    write('catalog.json',json.dumps({'ocr.default':{'driver':'gpustack.ocr','revision':'ocr-p2-r2','max_runtime_s':25,
        'upstream_model':ids['name'],'pin_file':'/trusted/pin.json','preflight_file':'/trusted/preflight.json'}}))
    url=(root/'.secrets/test.env').read_text().strip().split('=',1)[1].replace('/maas_test','/'+database)
    write('app.env','\n'.join(['MAAS_DATABASE_URL='+url,'MAAS_ARTIFACT_ROOT=/work/p2r2-artifacts','MAAS_GPUSTACK_URL=http://127.0.0.1:18080',
        'MAAS_BINDINGS_FILE=/work/.secrets/p2r2/bindings.json','MAAS_CATALOG_FILE=/work/.secrets/p2r2/catalog.json','MAAS_PEPPER_FILE=/work/.secrets/p2r2/pepper'])+'\n')
    run(*docker_base()[:-1],'-d','--name',containers[0],docker_base()[-1],'/work/.venv/bin/uvicorn','maas_platform.api:create_app','--factory','--host','127.0.0.1','--port','18830')
    wait(lambda:request('/healthz')[0]==200,30)
    image=Path('/root/wtaobj/service-validation/fixtures/upright.png').read_bytes()
    code,art=request('/maas/v1/artifacts',image,key,{'Content-Type':'image/png'});assert code==201
    payload={'model':'ocr.default','input':{'image':{'artifact_id':art['id']}},'timeout_seconds':120}
    ident=submit(key,'baseline');execute();row=state(ident);assert row['state']=='SUCCEEDED',row
    report['checks']['host_collector_real_task_success']=True
    saved_result=row['result_artifact_id']
    if smoke_only:
        result=request('/maas/v1/artifacts/'+saved_result,key=key)[1]
        assert result['outputs']['lines']
        report['checks']['persisted_result_lines']=len(result['outputs']['lines'])
        report['ok']=True
        raise SmokeComplete()
    # Revocation after acceptance must be checked again before dispatch.
    ident=submit(key,'permission-removed')
    admin('/v2/api-keys/'+str(keyinfo['id']),{'allowed_model_names':['rapidocr-gpu']},'PUT')
    execute();report['checks']['queued_permission_revocation']=assert_no_dispatch(ident,'AUTH_DENIED')
    admin('/v2/api-keys/'+str(keyinfo['id']),{'allowed_model_names':[ids['name'],'rapidocr-gpu']},'PUT')
    ident=submit(key,'binding-disabled');bindings['credentials'][0]['enabled']=False;write('bindings.json',json.dumps(bindings))
    execute();report['checks']['queued_binding_revocation']=assert_no_dispatch(ident,'AUTH_REVOKED')
    bindings['credentials'][0]['enabled']=True;write('bindings.json',json.dumps(bindings))
    # Do not regenerate the pin when actual deployment metadata changes.
    pin_before=hashlib.sha256((runtime/'pin.json').read_bytes()).hexdigest()
    m=admin('/v2/models/'+str(ids['model']));original_env=m.get('env');m['env']={**(original_env or {}),'MAAS_P2_DRIFT':'controlled-test'}
    admin('/v2/models/'+str(ids['model']),m,'PUT')
    wait(lambda:not (runtime/'preflight.json').exists(),30)
    ident=submit(key,'control-drift');execute();report['checks']['actual_deployment_drift']=assert_no_dispatch(ident,'PREFLIGHT_UNAVAILABLE')
    assert hashlib.sha256((runtime/'pin.json').read_bytes()).hexdigest()==pin_before
    m=admin('/v2/models/'+str(ids['model']));m['env']=original_env;admin('/v2/models/'+str(ids['model']),m,'PUT');original_env=None
    wait(healthy)
    report['checks']['restored_spec_recovers_without_repin']=True
    # Stop our own collector, not a GPUStack worker. Old metadata must expire.
    run('systemctl','stop','maas-collector.service')
    wait(lambda:time.time()-json.loads((runtime/'preflight.json').read_text())['observed_at']>31,40)
    ident=submit(key,'stale-collector');execute();report['checks']['stopped_collector_expiry']=assert_no_dispatch(ident,'PREFLIGHT_STALE')
    run('systemctl','start','maas-collector.service');wait(healthy)
    # Rebuild only the dedicated pilot instance on 001.
    instances=admin('/v2/models/'+str(ids['model'])+'/instances')['items']
    instance=next(x for x in instances if x['worker_name']=='XZ-31-001')
    name=instance['name']+'-run-0';assert name.startswith('maas-ocr-p2-r2-')
    old_id=run('docker','inspect','--format','{{.Id}}',name).strip()
    run('docker','rm','-f',name)
    def rebuilt():
        rows=admin('/v2/models/'+str(ids['model'])+'/instances')['items']
        if len(rows)!=2 or any(x['state']!='running' for x in rows):return False
        one=next((x for x in rows if x['worker_name']=='XZ-31-001'),None)
        if not one:return False
        found=subprocess.run(['docker','inspect','--format','{{.Id}}',one['name']+'-run-0'],capture_output=True,text=True)
        return one if found.returncode==0 and found.stdout.strip()!=old_id else False
    replacement=wait(rebuilt,150);wait(healthy)
    data=json.dumps({'image_base64':base64.b64encode(image).decode()}).encode()
    with urllib.request.urlopen(urllib.request.Request('http://'+replacement['worker_ip']+':'+str(replacement['port'])+'/ocr',data=data,headers={'Content-Type':'application/json'}),timeout=30) as response:
        direct=json.load(response);assert direct['lines']
    ident=submit(key,'after-rebuild');execute();assert state(ident)['state']=='SUCCEEDED'
    assert request('/maas/v1/artifacts/'+saved_result,key=key)[0]==200
    report['checks']['dedicated_instance_rebuilt']={'old_instance':instance['id'],'new_instance':replacement['id'],'new_container_id':True,'direct_ocr_lines':len(direct['lines'])}
    report['checks']['old_result_available_after_rebuild']=True
    # Delete the actual upstream key after queue acceptance.
    ident=submit(key,'key-deleted');admin('/v2/api-keys/'+str(keyinfo['id']),method='DELETE');res.created.remove(('/v2/api-keys',keyinfo['id']))
    execute();report['checks']['queued_upstream_key_deleted']=assert_no_dispatch(ident,'AUTH_DENIED')
    report['successful_events']=int(sql('SELECT count(*) FROM task_usage_events'))
    report['unknown_activities']=int(sql("SELECT count(*) FROM backend_activity WHERE state='UNKNOWN'"))
    assert report['successful_events']==2 and report['unknown_activities']==0
    report['ok']=True
except SmokeComplete:
    pass
finally:
    if original_env is not None:
        m=admin('/v2/models/'+str(ids['model']));m['env']=original_env;admin('/v2/models/'+str(ids['model']),m,'PUT')
    subprocess.run(['systemctl','start','maas-collector.service'],stdout=subprocess.DEVNULL)
    for name in containers:subprocess.run(['docker','rm','-f',name],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
    report['temporary_identity_cleanup']=res.cleanup();report['cleanup_failures']=res.failures
    for p in private.glob('*'):p.unlink()
    (runtime/('runtime-smoke.json' if smoke_only else 'fault-matrix.json')).write_text(json.dumps(report,indent=2)+'\n')
print(json.dumps(report))
