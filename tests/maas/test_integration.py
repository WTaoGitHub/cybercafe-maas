"""Run against a disposable PostgreSQL database, never the GPUStack database."""
import json, os, threading, time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import httpx, pytest
from fastapi.testclient import TestClient
from maas_platform.api import create_app
from maas_platform.auth import AuthConnector
from maas_platform.config import Settings
from maas_platform.errors import ServiceError
from maas_platform.schemas import TaskCreate
from maas_platform.worker import Worker


@pytest.fixture
def env(tmp_path):
    url=os.environ.get('MAAS_TEST_DATABASE_URL')
    if not url: pytest.skip('Requires disposable PostgreSQL via MAAS_TEST_DATABASE_URL')
    import psycopg
    # Database name must explicitly mark this as a test DB.
    assert psycopg.conninfo.conninfo_to_dict(url)['dbname'].endswith('_test')
    with psycopg.connect(url) as c:
        c.execute('DROP SCHEMA public CASCADE');c.execute('CREATE SCHEMA public')
    def secret(name,value):
        p=tmp_path/name;p.write_text(value);p.chmod(0o600);return p
    a=secret('key-a','gpustack_registered_a');b=secret('key-b','gpustack_registered_b')
    bindings=secret('bindings',json.dumps({'credentials':[
        {'id':'a','project':'a','secret_file':str(a),'models':['mock.echo']},
        {'id':'b','project':'b','secret_file':str(b),'models':['mock.echo']}]}))
    catalog=tmp_path/'catalog.json';catalog.write_text(json.dumps({'mock.echo':{'driver':'mock','revision':'1','max_runtime_s':30}}))
    settings=Settings(url,tmp_path/'artifacts','http://gpustack.test',bindings,catalog,secret('pepper','test-pepper'))
    settings.min_free_bytes=0
    revoked=set()
    def upstream(request):
        key=request.headers.get('authorization','')[7:]
        if key in revoked:return httpx.Response(401)
        return httpx.Response(200,json={'data':[]})
    auth=AuthConnector(settings,httpx.Client(transport=httpx.MockTransport(upstream)))
    app=create_app(settings,auth)
    with TestClient(app,raise_server_exceptions=False) as client:
        yield client,app.state.service,settings,auth,revoked


def headers(key='a',idem=None):
    h={'Authorization':'Bearer gpustack_registered_'+key}
    if idem:h['Idempotency-Key']=idem
    return h


def submit(client,idem='one',key='a',**kw):
    body={'model':'mock.echo','input':{'text':'hello'},**kw}
    return client.post('/maas/v1/tasks',headers=headers(key,idem),json=body)


def test_end_to_end_artifact_and_restarted_api(env):
    client,s,settings,auth,_=env
    upload=client.post('/maas/v1/artifacts',headers={**headers(),'Content-Type':'text/plain'},content=b'hello input');assert upload.status_code==201
    task=submit(client,input={'text':'hello','artifact_id':upload.json()['id']});assert task.status_code==202
    ident=task.json()['id']
    with TestClient(create_app(settings,auth)) as restarted:
        assert restarted.get('/maas/v1/tasks/'+ident,headers=headers()).json()['state']=='QUEUED'
        assert Worker(restarted.app.state.service).run_once()
        row=restarted.get('/maas/v1/tasks/'+ident,headers=headers()).json();assert row['state']=='SUCCEEDED'
        result=restarted.get('/maas/v1/artifacts/'+row['result_artifact_id'],headers=headers())
        assert result.json()['outputs']['input_artifact']['bytes']==11
    assert s.metrics()['successful_tasks']==1


def test_concurrent_idempotency_and_conflict(env):
    client,s,*_=env
    def create(_):return submit(client)
    with ThreadPoolExecutor(max_workers=8) as pool:responses=list(pool.map(create,range(8)))
    assert sum(r.status_code==202 for r in responses)==1
    assert len({r.json()['id'] for r in responses})==1
    assert submit(client,input={'text':'different'}).status_code==409
    assert submit(client,key='b').status_code==202


def test_auth_project_isolation_revocation(env):
    client,s,settings,auth,revoked=env
    task=submit(client).json();ident=task['id']
    assert client.get('/maas/v1/tasks/'+ident,headers=headers('b')).status_code==404
    assert client.get('/maas/v1/tasks/'+ident,headers={'X-Project':'a'}).status_code==401
    artifact=client.post('/maas/v1/artifacts',headers=headers(),content=b'x').json()['id']
    assert client.get('/maas/v1/artifacts/'+artifact,headers=headers('b')).status_code==404
    assert submit(client,idem='foreign',key='b',input={'artifact_id':artifact}).status_code==404
    revoked.add('gpustack_registered_a')
    assert client.get('/maas/v1/tasks/'+ident,headers=headers()).status_code==403
    assert Worker(s).run_once();assert s.metrics()['tasks']['FAILED']==1


def test_fencing_expired_attempt_and_once_metering(env):
    client,s,*_=env;submit(client)
    old=s.claim()
    with s.db.transaction() as c:c.execute("UPDATE tasks SET lease_until=now()-interval '1 second' WHERE id=%s",(old['id'],))
    assert not s.heartbeat(old)
    assert s.recover()==1
    new=s.claim();assert new['attempts']==2
    assert not s.finish(old,output={'old':True})
    assert s.finish(new,output={'new':True})
    assert not s.finish(new,output={'again':True})
    assert s.metrics()['successful_tasks']==1


def test_cancellation_queued_and_running(env):
    client,s,*_=env
    row=submit(client).json();assert client.post('/maas/v1/tasks/'+row['id']+'/cancel',headers=headers()).json()['state']=='CANCELLED'
    row=submit(client,idem='two').json();task=s.claim()
    assert client.post('/maas/v1/tasks/'+row['id']+'/cancel',headers=headers()).json()['state']=='CANCEL_REQUESTED'
    s.finish(task,output={'ignored':True})
    assert s.get(s.auth.authenticate('gpustack_registered_a'),row['id'])['state']=='CANCELLED'
    assert s.metrics()['successful_tasks']==0


def test_retry_budget_and_success(env):
    client,s,*_=env
    submit(client,parameters={'fail_first_attempts':1});assert Worker(s).run_once()
    assert s.metrics()['tasks']['RETRY_WAIT']==1
    with s.db.transaction() as c:c.execute('UPDATE tasks SET next_run=now()')
    assert Worker(s).run_once();assert s.metrics()['successful_tasks']==1
    submit(client,idem='exhaust',parameters={'fail_first_attempts':3},max_attempts=2)
    for _ in range(2):
        Worker(s).run_once()
        with s.db.transaction() as c:c.execute('UPDATE tasks SET next_run=now()')
    assert s.metrics()['tasks']['FAILED']==1


def test_pause_quota_deadline_and_ttl(env):
    client,s,settings,*_=env;settings.max_project_queued=1
    row=submit(client).json();assert submit(client,idem='over').status_code==429
    s.pause(True,'test maintenance');assert s.claim() is None
    s.pause(False)
    with s.db.transaction() as c:c.execute("UPDATE tasks SET deadline=now()-interval '1 second'")
    assert s.claim() is None;assert s.metrics()['tasks']['FAILED']==1
    art=client.post('/maas/v1/artifacts',headers=headers(),content=b'input').json()['id']
    row=submit(client,idem='with-art',input={'artifact_id':art}).json()
    with s.db.transaction() as c:c.execute("UPDATE artifacts SET expires_at=now()-interval '1 second' WHERE id=%s",(art,))
    assert s.cleanup()==0;assert s.store.path(art).exists()
    client.post('/maas/v1/tasks/'+row['id']+'/cancel',headers=headers());assert s.cleanup()==1
    assert client.get('/maas/v1/artifacts/'+art,headers=headers()).status_code==410


def test_revision_change_rejected_and_output_corruption(env):
    client,s,settings,*_=env;submit(client)
    model=json.loads(settings.catalog_file.read_text());model['mock.echo']['revision']='2';settings.catalog_file.write_text(json.dumps(model))
    assert Worker(s).run_once()
    with s.db.transaction() as c:assert c.execute('SELECT error_code FROM tasks').fetchone()['error_code']=='MODEL_REVISION_CHANGED'
    task=submit(client,idem='new').json();Worker(s).run_once()
    row=client.get('/maas/v1/tasks/'+task['id'],headers=headers()).json();s.store.path(row['result_artifact_id']).write_bytes(b'bad')
    assert client.get('/maas/v1/artifacts/'+row['result_artifact_id'],headers=headers()).status_code==503


def test_slot_limits_and_queue_wait_fairness(env):
    client,s,settings,*_=env
    submit(client,idem='a1');submit(client,idem='a2');submit(client,key='b',idem='b1')
    first=s.claim();second=s.claim();assert {first['project'],second['project']}=={'a','b'}
    assert s.claim() is None


def test_upload_and_parameters_limits(env):
    client,s,settings,*_=env;settings.max_input_bytes=5
    assert client.post('/maas/v1/artifacts',headers=headers(),content=b'123456').status_code==413
    assert client.post('/maas/v1/artifacts',headers={**headers(),'Content-Type':'text/html'},content=b'123').status_code==415
    assert submit(client,parameters={'delay_seconds':1000}).status_code==422
    assert submit(client,model='not-registered').status_code==404


def test_active_input_pinned_after_expiry(env):
    client,s,*_=env
    art=client.post('/maas/v1/artifacts',headers=headers(),content=b'pin me').json()['id']
    row=submit(client,input={'artifact_id':art}).json()
    with s.db.transaction() as c:c.execute("UPDATE artifacts SET expires_at=now()-interval '1 second' WHERE id=%s",(art,))
    assert s.cleanup()==0
    assert Worker(s).run_once()
    assert client.get('/maas/v1/tasks/'+row['id'],headers=headers()).json()['state']=='SUCCEEDED'


def test_chunked_json_limit_and_local_disable(env):
    client,s,settings,*_=env
    response=client.post('/maas/v1/tasks',headers={**headers(),'Content-Type':'application/json'},content=iter([b'x'*40000,b'x'*40000]))
    assert response.status_code==413
    assert response.json()['error']['code']=='REQUEST_SIZE'
    assert response.json()['error']['request_id']==response.headers['x-request-id']
    data=json.loads(settings.bindings_file.read_text());data['credentials'][0]['enabled']=False;settings.bindings_file.write_text(json.dumps(data))
    assert submit(client).status_code==401


def test_heartbeat_keeps_live_attempt_and_schema_errors(env):
    client,s,settings,*_=env
    row=submit(client).json();task=s.claim()
    assert s.heartbeat(task);assert s.recover()==0
    assert s.finish(task,output={'ok':True})
    response=client.post('/maas/v1/tasks',headers=headers(idem='bad'),json={'model':'mock.echo'})
    assert response.status_code==422 and response.json()['error']['code']=='INPUT_INVALID'


def test_storage_failure_does_not_create_task_or_success(env):
    client,s,settings,*_=env
    s.store.min_free_bytes=10**18
    assert submit(client).status_code==503
    assert s.metrics()['tasks']=={}
    s.store.min_free_bytes=0
    submit(client);task=s.claim();s.store.min_free_bytes=10**18
    with pytest.raises(ServiceError):s.finish(task,output={'ok':True})
    assert s.metrics()['successful_tasks']==0


def test_running_cancellation_with_worker_heartbeat(env):
    client,s,settings,*_=env;settings.heartbeat_seconds=.05
    task=submit(client,parameters={'delay_seconds':2}).json()
    thread=threading.Thread(target=Worker(s).run_once);thread.start()
    for _ in range(100):
        row=client.get('/maas/v1/tasks/'+task['id'],headers=headers()).json()
        if row['state']=='RUNNING':break
        time.sleep(.01)
    client.post('/maas/v1/tasks/'+task['id']+'/cancel',headers=headers())
    thread.join(timeout=5);assert not thread.is_alive()
    assert client.get('/maas/v1/tasks/'+task['id'],headers=headers()).json()['state']=='CANCELLED'
    assert s.metrics()['successful_tasks']==0


def test_second_adapter_has_independent_payload_and_capabilities(env):
    import hashlib
    client,s,settings,auth,_=env
    catalog=json.loads(settings.catalog_file.read_text())
    catalog['digest']={'driver':'local.sha256','revision':'digest-1','max_runtime_s':5}
    settings.catalog_file.write_text(json.dumps(catalog))
    bindings=json.loads(settings.bindings_file.read_text())
    bindings['credentials'][0]['models'].append('digest')
    settings.bindings_file.write_text(json.dumps(bindings))
    cap=client.get('/maas/v1/models/digest/capabilities',headers=headers()).json()
    assert 'source_artifact' in cap['input_schema']['required']
    assert 'text' not in cap['input_schema']['properties']
    assert submit(client,'wrong',model='digest').status_code==422
    uploaded=client.post('/maas/v1/artifacts',headers={**headers(),'Content-Type':'text/plain'},content=b'digest me').json()
    task=submit(client,'digest',model='digest',input={'source_artifact':uploaded['id']})
    assert task.status_code==202
    assert Worker(s).run_once()
    row=client.get('/maas/v1/tasks/'+task.json()['id'],headers=headers()).json()
    assert row['state']=='SUCCEEDED'
    result=client.get('/maas/v1/artifacts/'+row['result_artifact_id'],headers=headers()).json()
    assert result['outputs']['sha256']==hashlib.sha256(b'digest me').hexdigest()
    assert result['model_revision']=='digest-1'


def _crash_finisher(service, task, window):
    """Fault injection stays in the child test process, never in production config."""
    from contextlib import contextmanager
    if window=='file':
        original=service.store.put
        def put(data):
            original(data)
            os._exit(71)
        service.store.put=put
    else:
        original=service.db.transaction
        @contextmanager
        def transaction():
            with original() as c:
                yield c
                if window=='before_commit': os._exit(72)
            os._exit(73)
        service.db.transaction=transaction
    service.finish(task,output={'outputs':{'text':'durable'}})


@pytest.mark.parametrize('window',['file','before_commit','after_commit'])
def test_result_process_crash_windows(env,window):
    import multiprocessing
    client,s,settings,auth,_=env
    task_id=submit(client).json()['id'];task=s.claim()
    child=multiprocessing.get_context('fork').Process(target=_crash_finisher,args=(s,task,window))
    child.start();child.join(10)
    if child.is_alive():child.kill();child.join();pytest.fail('Fault child hung')
    assert child.exitcode in (71,72,73)
    row=s.get(auth.authenticate('gpustack_registered_a'),task_id)
    with s.db.transaction() as c:
        count=c.execute('SELECT count(*) n FROM task_usage_events').fetchone()['n']
        if window!='after_commit':
            assert row['state']=='RUNNING' and count==0
            c.execute("UPDATE tasks SET lease_until=now()-interval '1 second' WHERE id=%s",(task_id,))
        else:
            assert row['state']=='SUCCEEDED' and count==1
    if window!='after_commit':
        assert s.recover()==1
        assert Worker(s).run_once()
    identity=auth.authenticate('gpustack_registered_a')
    row=s.get(identity,task_id)
    assert row['state']=='SUCCEEDED'
    data,_=s.artifact(identity,row['result_artifact_id']);assert json.loads(data)['outputs']['text'] in ('hello','durable')
    assert s.finish(task,output={'stale':True}) is False
    with s.db.transaction() as c:
        assert c.execute('SELECT count(*) n FROM task_usage_events').fetchone()['n']==1


def test_upload_commit_ack_loss_keeps_referenced_file(env):
    from contextlib import contextmanager
    client,s,settings,auth,_=env
    original=s.db.transaction
    @contextmanager
    def lost_ack():
        with original() as c:yield c
        raise ConnectionError('Simulated lost commit acknowledgement')
    s.db.transaction=lost_ack
    with pytest.raises(ConnectionError):s.upload(auth.authenticate('gpustack_registered_a'),b'committed','text/plain')
    s.db.transaction=original
    with original() as c:row=c.execute("SELECT * FROM artifacts WHERE kind='input'").fetchone()
    assert s.store.path(row['id']).read_bytes()==b'committed'


def test_real_postgres_interruption(env):
    marker=os.environ.get('MAAS_DB_FAULT_MARKER')
    if not marker:pytest.skip('Requires the isolated host-side database interruption harness')
    marker=Path(marker)
    def wait(name):
        deadline=time.monotonic()+25
        while not marker.with_suffix('.'+name).exists():
            if time.monotonic()>deadline:pytest.fail('Database harness timed out: '+name)
            time.sleep(.1)
    client,s,settings,auth,_=env
    ident=submit(client,'survive-db').json()['id'];old=s.claim()
    marker.with_suffix('.ready').touch();wait('stopped')
    assert client.get('/maas/v1/tasks/'+ident,headers=headers()).status_code==503
    assert submit(client,'during-db-outage').status_code==503
    with pytest.raises(Exception):s.heartbeat(old)
    marker.with_suffix('.observed').touch();wait('restarted')
    row=client.get('/maas/v1/tasks/'+ident,headers=headers()).json()
    assert row['state']=='RUNNING'
    with s.db.transaction() as c:
        assert c.execute('SELECT count(*) n FROM tasks').fetchone()['n']==1
        c.execute("UPDATE tasks SET lease_until=now()-interval '1 second' WHERE id=%s",(ident,))
    assert s.recover()==1
    assert Worker(s).run_once()
    row=client.get('/maas/v1/tasks/'+ident,headers=headers()).json()
    assert row['state']=='SUCCEEDED' and row['attempts']==2
    assert not s.finish(old,output={'stale':True})
    with s.db.transaction() as c:
        assert c.execute('SELECT count(*) n FROM task_usage_events').fetchone()['n']==1


@pytest.mark.parametrize('cancel',[False,True])
def test_remote_uncertainty_quarantines_dispatch_and_requires_evidence(env,cancel):
    client,s,settings,auth,_=env
    task=submit(client).json();attempt=s.claim();s.begin_backend(attempt)
    if cancel:s.cancel(auth.authenticate('gpustack_registered_a'),task['id'])
    s.finish(attempt,error=ServiceError('TIMEOUT','Transport unavailable',503,True))
    row=client.get('/maas/v1/tasks/'+task['id'],headers=headers()).json()
    assert row['state']=='FAILED' and row['error_code']=='UNKNOWN_BACKEND_ACTIVITY'
    assert s.metrics()['unknown_backend_activity']==1
    assert submit(client,'waiting').status_code==202
    assert s.claim() is None
    s.pause(False,'resume must not release unknown work');assert s.claim() is None
    with pytest.raises(ValueError):s.resolve_backend(attempt['token'],'trust me')
    assert s.resolve_backend(attempt['token'],'Confirmed all dedicated backend processes have terminated')
    assert Worker(s).run_once()
    assert s.metrics()['successful_tasks']==1


@pytest.mark.parametrize('completed',[False,True])
def test_expired_remote_attempt_is_never_automatically_replayed(env,completed):
    client,s,settings,auth,_=env
    ident=submit(client).json()['id'];attempt=s.claim();s.begin_backend(attempt)
    if completed:s.complete_backend(attempt)
    with s.db.transaction() as c:
        c.execute("UPDATE tasks SET lease_until=now()-interval '1 second' WHERE id=%s",(ident,))
    assert s.recover()==1
    row=client.get('/maas/v1/tasks/'+ident,headers=headers()).json()
    assert row['state']=='FAILED' and row['attempts']==1
    assert row['error_code']==('RESULT_NOT_COMMITTED' if completed else 'UNKNOWN_BACKEND_ACTIVITY')
    assert s.metrics()['unknown_backend_activity']==(0 if completed else 1)
    assert not s.finish(attempt,output={'late':True})
    assert s.metrics()['successful_tasks']==0


def _exit_after_dispatch(service,task):
    service.begin_backend(task)
    os._exit(74)


def test_executor_process_exit_after_remote_dispatch_intent_preserves_quarantine(env):
    import multiprocessing
    client,s,settings,auth,_=env
    ident=submit(client).json()['id'];attempt=s.claim()
    child=multiprocessing.get_context('fork').Process(target=_exit_after_dispatch,args=(s,attempt))
    child.start();child.join(10)
    if child.is_alive():child.kill();child.join();pytest.fail('Dispatch child hung')
    assert child.exitcode==74
    with s.db.transaction() as c:
        c.execute("UPDATE tasks SET lease_until=now()-interval '1 second' WHERE id=%s",(ident,))
    assert s.recover()==1
    assert s.metrics()['unknown_backend_activity']==1
    assert submit(client,'pending').status_code==202 and s.claim() is None
    row=client.get('/maas/v1/tasks/'+ident,headers=headers()).json()
    assert row['state']=='FAILED' and row['error_code']=='UNKNOWN_BACKEND_ACTIVITY'


def test_quarantine_commit_serializes_with_other_remote_dispatch(env):
    from contextlib import contextmanager
    client,s,settings,auth,_=env
    submit(client,'remote-a');submit(client,'remote-b',key='b')
    first=s.claim();second=s.claim();assert second
    s.begin_backend(first)
    original=s.db.transaction;ready=threading.Event();release=threading.Event();done=threading.Event();errors=[]
    @contextmanager
    def transaction():
        with original() as c:
            yield c
            if threading.current_thread().name=='quarantine-finisher':
                ready.set();assert release.wait(3)
    s.db.transaction=transaction
    finisher=threading.Thread(name='quarantine-finisher',target=lambda:s.finish(first,error=ServiceError('TIMEOUT','unknown')))
    def dispatch():
        try:s.begin_backend(second)
        except ServiceError as exc:errors.append(exc.code)
        finally:done.set()
    contender=threading.Thread(target=dispatch)
    try:
        finisher.start();assert ready.wait(3)
        contender.start();assert not done.wait(.1)
    finally:
        release.set();finisher.join(5)
        if contender.ident:contender.join(5)
        s.db.transaction=original
    assert not finisher.is_alive() and not contender.is_alive()
    assert errors==['BACKEND_QUARANTINED']
    with original() as c:
        assert c.execute('SELECT count(*) n FROM backend_activity').fetchone()['n']==1


def test_pause_after_claim_defers_unsent_attempt_without_spending_retry(env):
    client,s,_,_,_=env
    ident=submit(client,max_attempts=1).json()['id'];attempt=s.claim()
    s.pause(True,'maintenance window')
    with pytest.raises(ServiceError,match='paused') as exc:s.begin_backend(attempt)
    assert exc.value.code=='MAINTENANCE_PAUSED'
    assert s.defer_dispatch(attempt)
    assert not s.finish(attempt,output={'late':True})
    assert s.claim() is None
    with s.db.transaction() as c:
        assert c.execute('SELECT count(*) n FROM backend_activity').fetchone()['n']==0
        assert c.execute('SELECT state FROM task_attempts').fetchone()['state']=='DEFERRED'
    s.pause(False);next_attempt=s.claim()
    assert next_attempt['attempts']==2
    # One real failed execution exhausts max_attempts=1, despite the deferred claim.
    assert s.finish(next_attempt,error=ServiceError('TRANSIENT','retryable',503,True))
    assert s.get(s.auth.authenticate('gpustack_registered_a'),ident)['state']=='FAILED'


def test_deferral_never_erases_dispatch_intent_and_honors_cancellation(env):
    client,s,_,auth,_=env
    first=submit(client).json();attempt=s.claim();s.begin_backend(attempt)
    s.pause(True,'pause after dispatch');assert not s.defer_dispatch(attempt)
    s.finish(attempt,error=ServiceError('TIMEOUT','uncertain'))
    assert s.metrics()['unknown_backend_activity']==1
    assert s.resolve_backend(attempt['token'],'Confirmed the test backend process is terminated')
    s.pause(False);second=submit(client,'second').json();pending=s.claim()
    s.pause(True);s.cancel(auth.authenticate('gpustack_registered_a'),second['id'])
    assert s.defer_dispatch(pending)
    assert s.get(auth.authenticate('gpustack_registered_a'),second['id'])['state']=='CANCELLED'


def sync_setup(env,monkeypatch,handler):
    client,s,settings,auth,_=env
    catalog={'driver':'gpustack.ocr','revision':'ocr-r1','max_runtime_s':5,'upstream_model':'real-ocr','pin_file':'/trusted/pin','preflight_file':'/trusted/preflight'}
    settings.catalog_file.write_text(json.dumps({'mock.echo':{'driver':'mock','revision':'1','max_runtime_s':30},'ocr.default':catalog}))
    bindings=json.loads(settings.bindings_file.read_text());bindings['credentials'][0]['models'].append('ocr.default')
    settings.bindings_file.write_text(json.dumps(bindings))
    monkeypatch.setattr('maas_platform.sync_ingress.preflight',lambda model:None)
    client.app.state.sync_ingress.client_factory=lambda **kw:httpx.AsyncClient(transport=httpx.MockTransport(handler),**kw)
    return client,s,settings


def test_sync_admission_pause_unknown_and_manual_resolution(env,monkeypatch):
    from maas_platform.maintenance import Maintenance
    def fail(request):raise httpx.ReadError('uncertain upstream')
    client,s,_=sync_setup(env,monkeypatch,fail)
    body={'model':'ocr.default','messages':[]}
    s.pause(True,'before sync');assert client.post('/v1/chat/completions',headers=headers(),json=body).status_code==503
    with s.db.transaction() as c:assert c.execute('SELECT count(*) n FROM sync_activity').fetchone()['n']==0
    s.pause(False)
    result=client.post('/v1/chat/completions',headers=headers(),json=body)
    assert result.status_code==503 and result.json()['error']['code']=='UNKNOWN_BACKEND_ACTIVITY'
    submit(client);assert s.claim() is None
    assert client.post('/v1/chat/completions',headers=headers(),json=body).json()['error']['code']=='BACKEND_QUARANTINED'
    s.pause(True);m=Maintenance(s);status=m.status()
    assert not status['managed_requests_drained'] and not status['safe_to_poweroff']
    with pytest.raises(ServiceError):m.resume(lambda:None)
    token=status['sync_activity'][0]['token']
    with pytest.raises(ValueError):m.resolve_sync(token,'trust me')
    assert m.resolve_sync(token,'Confirmed backend processes are stopped and cannot respawn')
    assert m.status()['managed_requests_drained']


def test_sync_success_mapping_capacity_and_no_secret_forwarding(env,monkeypatch):
    from maas_platform.maintenance import Maintenance
    calls=[]
    def ok(request):
        calls.append(request)
        assert json.loads(request.content)['model']=='real-ocr'
        assert request.headers.get('x-project') is None
        return httpx.Response(200,json={'choices':[]})
    client,s,_=sync_setup(env,monkeypatch,ok)
    response=client.post('/v1/chat/completions',headers={**headers(),'X-Project':'evil'},json={'model':'ocr.default','messages':[]})
    assert response.status_code==200 and len(calls)==1
    s.pause(True);status=Maintenance(s).status();assert status['managed_requests_drained'] and not status['safe_to_poweroff']
    assert client.post('/v1/chat/completions',headers=headers(),json={'model':'ocr.default','stream':True}).status_code==422
    assert client.post('/v1/chat/completions',headers=headers('b'),json={'model':'ocr.default'}).status_code==403
    s.pause(False)
    ingress=client.app.state.sync_ingress;who=s.auth.authenticate('gpustack_registered_a')
    token=ingress.begin(who,'ocr.default',5)
    assert client.post('/v1/chat/completions',headers=headers(),json={'model':'ocr.default'}).status_code==429
    ingress.finish(token,True)


@pytest.mark.parametrize('kind',['bad-status','oversize','compressed','crash'])
def test_sync_uncertain_work_prevents_drain(env,monkeypatch,kind):
    from maas_platform.maintenance import Maintenance
    def reply(request):
        if kind=='bad-status':return httpx.Response(503)
        if kind=='oversize':return httpx.Response(200,content=b'x'*100)
        return httpx.Response(200,headers={'Content-Encoding':'gzip'},content=b'not-gzip')
    client,s,settings=sync_setup(env,monkeypatch,reply)
    if kind=='oversize':settings.max_output_bytes=10
    if kind=='crash':
        who=s.auth.authenticate('gpustack_registered_a');client.app.state.sync_ingress.begin(who,'ocr.default',5)
        with s.db.transaction() as c:c.execute("UPDATE sync_activity SET deadline=now()-interval '1 second'")
    else:assert client.post('/v1/chat/completions',headers=headers(),json={'model':'ocr.default'}).status_code in (502,503)
    s.pause(True);status=Maintenance(s).status()
    assert status['sync_activity'][0]['state']=='UNKNOWN'
    assert not status['managed_requests_drained']


def test_maintenance_recovery_probe_failure_generation_and_active_task(env):
    from maas_platform.maintenance import Maintenance
    client,s,*_=env;m=Maintenance(s)
    submit(client);attempt=s.claim();s.pause(True,'window one')
    assert not m.status()['managed_requests_drained']
    with pytest.raises(ServiceError):m.resume(lambda:None)
    s.defer_dispatch(attempt)
    def failed():raise ServiceError('PROBE_FAILED','probe failed')
    with pytest.raises(ServiceError):m.resume(failed)
    assert m.status()['paused']
    with pytest.raises(ServiceError) as exc:m.resume(lambda:s.pause(True,'new maintenance window'))
    assert exc.value.code=='MAINTENANCE_CHANGED' and m.status()['paused']
    assert not m.resume(lambda:None)['paused']
    assert Worker(s).run_once()


def test_recovery_runs_real_transport_validates_output_and_keeps_gate_closed(env,monkeypatch,tmp_path):
    from maas_platform.maintenance import Maintenance
    from maas_platform.recovery import probe_registered_models
    valid={'text':'ok','lines':[],'elapsed_ms':1,'node':'002'}
    calls=[]
    def reply(request):
        calls.append(request)
        assert Maintenance(s).status()['paused']
        return httpx.Response(200,json={'choices':[{'message':{'content':json.dumps(valid)}}]})
    client,s,_=sync_setup(env,monkeypatch,reply)
    monkeypatch.setattr('maas_platform.recovery.SyncIngress',lambda service:client.app.state.sync_ingress)
    image=tmp_path/'probe.png';image.write_bytes(b'\x89PNG\r\n\x1a\nfixture')
    s.pause(True,'recovery window')
    with pytest.raises(ValueError):Maintenance(s).resume(lambda:probe_registered_models(s,None,None))
    assert Maintenance(s).status()['paused'] and not calls
    Maintenance(s).resume(lambda:probe_registered_models(s,'a',image))
    assert len(calls)==1 and not Maintenance(s).status()['paused']
    s.pause(True,'invalid probe output');valid.clear()
    with pytest.raises(ServiceError):Maintenance(s).resume(lambda:probe_registered_models(s,'a',image))
    assert Maintenance(s).status()['paused']


def test_cross_channel_project_and_global_capacity(env):
    from maas_platform.sync_ingress import SyncIngress
    client,s,settings,auth,_=env
    ingress=SyncIngress(s);a=auth.authenticate('gpustack_registered_a');b=auth.authenticate('gpustack_registered_b')
    assert submit(client).status_code==202
    token=ingress.begin(a,'mock.echo',30)
    assert s.claim() is None  # project a already occupies its single slot
    other=ingress.begin(b,'mock.echo',30)
    assert s.claim() is None  # global two slots exhausted
    ingress.finish(token,True)
    task=s.claim();assert task
    with pytest.raises(ServiceError) as exc:ingress.begin(a,'mock.echo',30)
    assert exc.value.code=='SYNC_CAPACITY'
    ingress.finish(other,True)
    with pytest.raises(ServiceError):ingress.begin(a,'mock.echo',30)
    s.finish(task,output={'ok':True})
    assert ingress.begin(a,'mock.echo',30)


def test_storage_quota_ttl_orphans_and_active_input(env):
    client,s,settings,auth,_=env
    a=auth.authenticate('gpustack_registered_a');b=auth.authenticate('gpustack_registered_b')
    settings.max_storage_bytes=8;settings.max_project_storage_bytes=5
    first=s.upload(a,b'12345','text/plain')
    with pytest.raises(ServiceError) as exc:s.upload(a,b'x','text/plain')
    assert exc.value.code=='STORAGE_QUOTA'
    s.upload(b,b'123','text/plain')
    with pytest.raises(ServiceError):s.upload(b,b'x','text/plain')
    with s.db.transaction() as c:c.execute("UPDATE artifacts SET expires_at=now()-interval '1 second'")
    assert s.cleanup()==2
    s.upload(a,b'12345','text/plain')
    orphan=s.store.put(b'lost');old=time.time()-settings.orphan_grace_seconds-10
    os.utime(s.store.path(orphan['id']),(old,old))
    os.utime(s.store.path(first['id']),(old,old)) if s.store.path(first['id']).exists() else None
    foreign=settings.artifact_root/'keep.txt';foreign.write_text('keep')
    link=settings.artifact_root/('art_'+'f'*32);link.symlink_to(foreign)
    assert s.cleanup_orphans()==1
    assert foreign.exists() and link.is_symlink()
    assert not s.store.path(orphan['id']).exists()
    assert s.cleanup_orphans()==0


def test_orphan_gc_waits_for_upload_commit(env,monkeypatch):
    client,s,settings,auth,_=env
    a=auth.authenticate('gpustack_registered_a')
    settings.orphan_grace_seconds=1
    original=s.store.put;written=threading.Event();release=threading.Event()
    def delayed(data):
        result=original(data);old=time.time()-10
        os.utime(s.store.path(result['id']),(old,old));written.set()
        assert release.wait(3)
        return result
    monkeypatch.setattr(s.store,'put',delayed)
    with ThreadPoolExecutor(2) as pool:
        upload=pool.submit(s.upload,a,b'ok','text/plain');assert written.wait(3)
        gc=pool.submit(s.cleanup_orphans);time.sleep(.1);assert not gc.done()
        release.set();record=upload.result();assert gc.result()==0
    assert s.store.path(record['id']).read_bytes()==b'ok'


def test_sync_usage_exactly_once_probe_and_failure_exclusion(env,monkeypatch):
    def ok(request):return httpx.Response(200,json={'choices':[]})
    client,s,_=sync_setup(env,monkeypatch,ok)
    assert client.post('/v1/chat/completions',headers=headers(),json={'model':'ocr.default'}).status_code==200
    ingress=client.app.state.sync_ingress;a=s.auth.authenticate('gpustack_registered_a')
    with s.db.transaction() as c:
        row=c.execute('SELECT * FROM sync_activity').fetchone()
        assert row['outcome']=='SUCCEEDED'
    ingress.finish(row['token'],True,True)
    s.pause(True)
    token=ingress.begin(a,'ocr.default',10,probe=True);ingress.finish(token,True,True)
    s.pause(False)
    rejected=ingress.begin(a,'ocr.default',10);ingress.finish(rejected,True,False)
    with s.db.transaction() as c:
        assert c.execute('SELECT count(*) n FROM sync_usage_events').fetchone()['n']==1
        assert c.execute('SELECT outcome FROM sync_activity WHERE token=%s',(rejected,)).fetchone()['outcome']=='REJECTED'


def test_private_metrics_and_actionable_alerts(env):
    from maas_platform.observability import snapshot,alerts,prometheus
    client,s,settings,auth,_=env
    submit(client)
    with s.db.transaction() as c:c.execute("UPDATE tasks SET created_at=now()-interval '301 seconds'")
    data=snapshot(s);assert data['oldest_queue_seconds']>=300
    assert 'QUEUE_WAIT' in {a['code'] for a in alerts(s,data)}
    s.pause(True);data=snapshot(s)
    assert 'MAINTENANCE_PAUSED' in {a['code'] for a in alerts(s,data)}
    metrics=prometheus(data)
    assert 'maas_dispatch_paused 1' in metrics
    assert 'gpustack_registered' not in metrics and 'project=' not in metrics
    assert client.get('/metrics',headers=headers()).status_code==404
    s.store.min_free_bytes=0
    settings.min_free_bytes=10**30
    assert 'DISK_LOW' in {a['code'] for a in alerts(s,snapshot(s))}
