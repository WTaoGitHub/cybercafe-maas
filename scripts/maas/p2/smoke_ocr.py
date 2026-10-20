"""Controller-only P2 acceptance. Temporary tenants, DB and containers; real GPU OCR.
Run refresh_preflight.py --loop locally while this test runs. Existing OCR is untouched.
"""
import json,os,secrets,socket,subprocess,sys,time,urllib.request,urllib.error
from pathlib import Path
sys.path.insert(0,'/root/wtaobj/maas-framework/scripts/maas/p0')
from common import Resources,admin
root=Path('/root/wtaobj/maas-framework');target_root=Path('/root/wtaobj/maas-p2')
target=json.loads((target_root/'target.json').read_text());res=Resources()
private=root/'.secrets'/'p2';private.mkdir(mode=0o700,exist_ok=True)
report={'stage':'P2-first-real-OCR','checks':{},'production_models_mutated':False}
containers=['wta-maas-p2-api','wta-maas-p2-executor']
for name in containers:
    if subprocess.run(['docker','inspect',name],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL).returncode==0:raise RuntimeError('Pilot name already in use')
probe=socket.socket();probe.bind(('127.0.0.1',18830));probe.close()
def write(name,value):
    path=private/name
    fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_TRUNC,0o600)
    with os.fdopen(fd,'w') as f:f.write(value)
def run(*args):return subprocess.check_output(list(args),text=True)
def request(path,body=None,key=None,headers=None):
    if isinstance(body,dict):body=json.dumps(body).encode()
    h={'Authorization':'Bearer '+key} if key else {}
    if body is not None:h['Content-Type']='application/json'
    try:
        with urllib.request.urlopen(urllib.request.Request('http://127.0.0.1:18830'+path,data=body,headers={**h,**(headers or {})}),timeout=20) as r:
            raw=r.read()
            return r.status,json.loads(raw) if 'json' in r.headers.get('Content-Type','') else raw
    except urllib.error.HTTPError as e:return e.code,json.load(e)
def start(name,command):
    run('docker','run','-d','--name',name,'--network','host','--env-file',str(private/'app.env'),
        '-v',str(root)+':/work','-v',str(target_root)+':/trusted:ro','-w','/work','python:3.11-slim',*command)
def terminal(ident,key):
    for _ in range(200):
        code,row=request('/maas/v1/tasks/'+ident,key=key)
        if row.get('state') in ('SUCCEEDED','FAILED','CANCELLED'):return row
        time.sleep(.2)
    raise RuntimeError('Task did not complete')
def db(sql):return run('docker','exec','wta-maas-p1-test-db','psql','-U','maas','-d','maas_p2_test','-tAc',sql).strip()
def scale(replicas):
    m=admin('/v2/models/'+str(target['model']));m['replicas']=replicas
    admin('/v2/models/'+str(target['model']),m,'PUT')
try:
    # Must not reuse an existing acceptance DB silently.
    run('docker','exec','wta-maas-p1-test-db','psql','-U','maas','-d','postgres','-c','CREATE DATABASE maas_p2_test')
    bindings=[];keys=[]
    for suffix in ('a','b'):
        user,op=res.user('ocr-'+suffix);_,key=res.key(op,'ocr-'+suffix,[target['name']],expires=1800)
        keys.append(key);write('key-'+suffix,key)
        bindings.append({'id':suffix,'project':suffix,'secret_file':'/work/.secrets/p2/key-'+suffix,'models':['ocr.default','mock.echo']})
    write('bindings.json',json.dumps({'credentials':bindings}));write('pepper',secrets.token_hex(32))
    write('catalog.json',json.dumps({'ocr.default':{'driver':'gpustack.ocr','revision':'ocr-p2-r1','max_runtime_s':25,
         'upstream_model':target['name'],'pin_file':'/trusted/pin.json','preflight_file':'/trusted/preflight.json'},
         'mock.echo':{'driver':'mock','revision':'1','max_runtime_s':10}}))
    url=(root/'.secrets/test.env').read_text().strip().split('=',1)[1].replace('/maas_test','/maas_p2_test')
    write('app.env','\n'.join(['MAAS_DATABASE_URL='+url,'MAAS_ARTIFACT_ROOT=/work/p2-artifacts',
        'MAAS_GPUSTACK_URL=http://127.0.0.1:18080','MAAS_BINDINGS_FILE=/work/.secrets/p2/bindings.json',
        'MAAS_CATALOG_FILE=/work/.secrets/p2/catalog.json','MAAS_PEPPER_FILE=/work/.secrets/p2/pepper',
        'MAAS_LEASE_SECONDS=6','MAAS_HEARTBEAT_SECONDS=1'])+'\n')
    start(containers[0],['/work/.venv/bin/uvicorn','maas_platform.api:create_app','--factory','--host','127.0.0.1','--port','18830'])
    for _ in range(100):
        try:
            if request('/healthz')[0]==200:break
        except Exception:pass
        time.sleep(.2)
    else:raise RuntimeError('Task API unavailable')
    image=Path('/root/wtaobj/service-validation/fixtures/upright.png').read_bytes()
    code,art=request('/maas/v1/artifacts',image,keys[0],{'Content-Type':'image/png'});assert code==201
    payload={'model':'ocr.default','input':{'image':{'artifact_id':art['id']}},'timeout_seconds':60,'max_attempts':3}
    code,row=request('/maas/v1/tasks',payload,keys[0],{'Idempotency-Key':'ocr-live'});assert code==202
    ident=row['id'];code,repeated=request('/maas/v1/tasks',payload,keys[0],{'Idempotency-Key':'ocr-live'})
    assert code==200 and repeated['id']==ident
    report['checks']['idempotent_acceptance']=True
    assert request('/maas/v1/tasks/'+ident,key=keys[1])[0]==404
    assert request('/maas/v1/artifacts/'+art['id'],key=keys[1])[0]==404
    report['checks']['cross_project_denied']=True
    start(containers[1],['/work/.venv/bin/python','-m','maas_platform.worker'])
    row=terminal(ident,keys[0]);assert row['state']=='SUCCEEDED',row
    code,result=request('/maas/v1/artifacts/'+row['result_artifact_id'],key=keys[0]);assert code==200
    assert result['outputs']['lines'] and result['outputs']['text']
    assert result['model_revision']=='ocr-p2-r1' and row['attempts']==1
    report['checks']['real_ocr_upload_task_download']={'lines':len(result['outputs']['lines']),'attempts':row['attempts'],'revision':result['model_revision']}
    assert db('SELECT count(*) FROM task_usage_events')=='1'
    assert db("SELECT count(*) FROM backend_activity WHERE state='COMPLETED'")=='1'
    report['checks']['successful_event_and_proven_completion_unique']=True
    # The second model uses the same task envelope but an independent payload.
    code,mock=request('/maas/v1/tasks',{'model':'mock.echo','input':{'text':'second model'}},keys[0],{'Idempotency-Key':'second-model'})
    assert code==202 and terminal(mock['id'],keys[0])['state']=='SUCCEEDED'
    report['checks']['second_model_same_api']=True
    # Freeze a verified observation within the 30-second freshness window.
    # The host coordinator pauses ONLY this dedicated model's containers.
    snapshot=target_root/'preflight.json';frozen=target_root/'outage-preflight.json';frozen.write_bytes(snapshot.read_bytes());frozen.chmod(0o600)
    catalog=json.loads((private/'catalog.json').read_text());catalog['ocr.default']['preflight_file']='/trusted/outage-preflight.json'
    catalog['ocr.default']['max_runtime_s']=3
    write('catalog.json',json.dumps(catalog))
    (target_root/'fault-ready').touch()
    def wait_marker(name):
        for _ in range(200):
            if (target_root/name).exists():return
            time.sleep(.1)
        raise RuntimeError('Fault coordinator timed out: '+name)
    wait_marker('fault-paused')

    code,failed=request('/maas/v1/tasks',payload,keys[0],{'Idempotency-Key':'backend-offline'});assert code==202
    offline=terminal(failed['id'],keys[0])
    assert offline['state']=='FAILED' and offline['attempts']==1,offline
    assert offline['error_code']=='UNKNOWN_BACKEND_ACTIVITY',offline
    report['checks']['backend_offline_no_application_replay']={'attempts':offline['attempts'],'error_code':offline['error_code']}
    assert db("SELECT count(*) FROM backend_activity WHERE state='UNKNOWN'")=='1'
    # Completed artifacts remain independent from inference worker availability.
    assert request('/maas/v1/artifacts/'+row['result_artifact_id'],key=keys[0])[0]==200
    report['checks']['stored_result_available_with_backends_offline']=True
    code,queued=request('/maas/v1/tasks',{'model':'mock.echo','input':{'text':'blocked by unknown'}},keys[0],{'Idempotency-Key':'quarantine'})
    time.sleep(1)
    assert request('/maas/v1/tasks/'+queued['id'],key=keys[0])[1]['state']=='QUEUED'
    report['checks']['unknown_activity_blocks_new_dispatch']=True
    (target_root/'fault-release').touch();wait_marker('fault-released')
    scale(0)
    for _ in range(100):
        if not admin('/v2/models/'+str(target['model'])+'/instances')['items']:break
        time.sleep(.2)
    else:raise RuntimeError('Dedicated model processes did not stop')
    (target_root/'fault-stopped').touch();wait_marker('fault-verified')
    token=db("SELECT token FROM backend_activity WHERE state='UNKNOWN'")
    run('docker','exec',containers[1],'/work/.venv/bin/python','-m','maas_platform.cli',
        'resolve-backend','--token',token,'--reason','Dedicated model scaled to zero; both node Docker engines confirmed no dedicated running processes')
    assert terminal(queued['id'],keys[0])['state']=='SUCCEEDED'
    report['checks']['evidence_based_resolution_resumes_dispatch']=True
    assert request('/maas/v1/artifacts/'+row['result_artifact_id'],key=keys[0])[0]==200
    report['checks']['result_available_after_both_dedicated_backends_removed']=True
    report['ok']=True
finally:
    for name in containers:subprocess.run(['docker','rm','-f',name],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
    report['temporary_credentials_removed']=res.cleanup()
    for file in private.glob('*'):file.unlink()
    (root/'p2-smoke.json').write_text(json.dumps(report,indent=2)+'\n')
print(json.dumps(report))
