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
