import json,time
from concurrent.futures import ThreadPoolExecutor
import httpx,pytest
from maas_platform.worker import Worker
from maas_platform.errors import ServiceError
from test_integration import env,headers,sync_setup
from test_inline_tasks import data_url


def setup(env,monkeypatch):
    client,s,settings=sync_setup(env,monkeypatch,lambda _:httpx.Response(200,json={}))
    factory=httpx.AsyncClient
    def upstream(req):
        assert json.loads(req.content)['messages'][0]['content'][0]['image_url']['url'].startswith('data:image/png;base64,')
        return httpx.Response(200,json={'choices':[{'message':{'content':json.dumps({'text':'hello','lines':[],'elapsed_ms':1,'node':'test'})}}]})
    monkeypatch.setattr('maas_platform.ocr_connector.httpx.AsyncClient',lambda **kw:factory(transport=httpx.MockTransport(upstream),**kw))
    return client,s,settings


def post(client,idem='inline-one',**change):
    body={'model':'ocr.default','input':{'image':{'image_base64':data_url()}},'max_attempts':1};body.update(change)
    return client.post('/maas/v1/tasks',headers=headers(idem=idem),json=body)


def counts(s):
    with s.db.transaction() as c:return [c.execute('SELECT count(*) n FROM '+table).fetchone()['n'] for table in ('tasks','artifacts')]


def test_inline_atomic_idempotency_execution_result_and_original_reference(env,monkeypatch):
    client,s,_=setup(env,monkeypatch)
    with ThreadPoolExecutor(max_workers=6) as pool:r=list(pool.map(lambda _:post(client),range(6)))
    assert sorted(x.status_code for x in r)==[200]*5+[202]
    assert len({x.json()['id'] for x in r})==1 and counts(s)==[1,1]
    with s.db.transaction() as c:
        row=c.execute('SELECT input,input_artifact_id FROM tasks').fetchone();ident=row['input_artifact_id']
        assert row['input']=={'image':{'artifact_id':ident}}
    assert post(client,input={'image':{'image_base64':data_url('black')}}).status_code==409
    assert Worker(s).run_once()
    task=client.get('/maas/v1/tasks/'+r[0].json()['id'],headers=headers()).json();assert task['state']=='SUCCEEDED'
    assert client.get('/maas/v1/artifacts/'+task['result_artifact_id'],headers=headers()).json()['outputs']['text']=='hello'
    assert post(client).json()['state']=='SUCCEEDED' and counts(s)==[1,2]
    old=post(client,'reference',input={'image':{'artifact_id':ident}});assert old.status_code==202
    assert Worker(s).run_once() and s.metrics()['successful_tasks']==2
    assert client.get('/maas/v1/tasks/'+task['id'],headers=headers('b')).status_code==404


@pytest.mark.parametrize('kind,code',[('format',422),('media',415),('pixels',413),('size',413),('params',422),('quota',429),('queue',429),('forbidden',403)])
def test_rejection_no_task_or_artifact(env,monkeypatch,kind,code):
    client,s,settings=setup(env,monkeypatch);body={}
    if kind=='format':body['input']={'image':{'image_base64':'not-data'}}
    if kind=='media':body['input']={'image':{'image_base64':data_url().replace('image/png','image/jpeg')}}
    if kind=='pixels':monkeypatch.setattr('maas_platform.consumer_ocr.MAX_PIXELS',1)
    if kind=='size':settings.max_input_bytes=2
    if kind=='params':body['parameters']={'unknown':1}
    if kind=='quota':settings.max_project_storage_bytes=1
    if kind=='queue':settings.max_queued=0
    if kind=='forbidden':
        response=client.post('/maas/v1/tasks',headers=headers('b','forbidden'),json={'model':'ocr.default','input':{'image':{'image_base64':data_url()}}})
    else:response=post(client,**body)
    assert response.status_code==code and counts(s)==[0,0]
    assert not list(s.store.root.glob('art_*'))


@pytest.mark.parametrize('kind',['write','transaction'])
def test_failure_orphan_gc_and_no_broken_reference(env,monkeypatch,kind):
    client,s,settings=setup(env,monkeypatch)
    if kind=='write':monkeypatch.setattr(s.store,'put',lambda _:(_ for _ in ()).throw(OSError('write failed')))
    else:
        put=s.store.put
        def fail_after_file(raw):
            put(raw);raise RuntimeError('metadata failure')
        monkeypatch.setattr(s.store,'put',fail_after_file)
    assert post(client).status_code==503 and counts(s)==[0,0]
    files=list(s.store.root.glob('art_*'));assert len(files)==(kind=='transaction')
    settings.orphan_grace_seconds=0;time.sleep(.05)
    for _ in range(3):s.cleanup_orphans()
    assert not list(s.store.root.glob('art_*'))


def test_large_inline_request_not_subject_to_old_64k_json_limit(env,monkeypatch):
    client,s,_=setup(env,monkeypatch)
    # A valid image followed by >64KiB whitespace in JSON still identifies the inline source.
    value={'model':'ocr.default','input':{'image':{'image_base64':data_url()}}}
    response=client.post('/maas/v1/tasks',headers={**headers(idem='large-inline'),'Content-Type':'application/json'},content=json.dumps(value)+' '*70000)
    assert response.status_code==202
    value['input']={'image':{'artifact_id':'art_example'}}
    assert client.post('/maas/v1/tasks',headers={**headers(idem='large-ref'),'Content-Type':'application/json'},content=json.dumps(value)+' '*70000).status_code==413


def test_commit_ack_loss_recovers_same_task_without_second_input(env,monkeypatch):
    from contextlib import contextmanager
    client,s,_=setup(env,monkeypatch);original=s.db.transaction;once=[True]
    @contextmanager
    def transaction():
        with original() as connection:yield connection
        if once[0]:
            once[0]=False
            raise RuntimeError('commit acknowledgement lost')
    monkeypatch.setattr(s.db,'transaction',transaction)
    assert post(client).status_code==503
    assert counts(s)==[1,1]
    assert post(client).status_code==200 and counts(s)==[1,1]
    assert Worker(s).run_once() and s.metrics()['successful_tasks']==1


def test_real_image_over_64k_capability_and_cancel(env,monkeypatch):
    import os,io,base64
    from PIL import Image
    client,s,_=setup(env,monkeypatch);out=io.BytesIO();Image.frombytes('RGB',(256,256),os.urandom(256*256*3)).save(out,format='PNG')
    raw=out.getvalue();assert len(raw)>65536
    source='data:image/png;base64,'+base64.b64encode(raw).decode()
    response=post(client,input={'image':{'image_base64':source}});assert response.status_code==202
    who=s.auth.authenticate('gpustack_registered_a')
    with s.db.transaction() as c:ident=c.execute('SELECT input_artifact_id FROM tasks').fetchone()['input_artifact_id']
    assert s.artifact(who,ident)==(raw,'image/png')
    assert client.post('/maas/v1/tasks/'+response.json()['id']+'/cancel',headers=headers()).json()['state']=='CANCELLED'
    assert post(client,input={'image':{'image_base64':source}}).json()['state']=='CANCELLED'
    schema=client.get('/maas/v1/models/ocr.default/capabilities',headers=headers()).json()['input_schema']
    assert 'image_base64' in schema['$defs']['InlineImage']['properties']


def test_bare_base64_and_deployed_legacy_key_remain_compatible(env,monkeypatch):
    client,s,_=setup(env,monkeypatch)
    for name in ('image_base64','data_url'):
        body={'model':'ocr.default','input':{'image':{name:data_url().split(',',1)[1]}},'max_attempts':1}
        r=client.post('/maas/v1/tasks',headers=headers(idem='rename-'+name),json=body)
        assert r.status_code==202 and Worker(s).run_once()
        assert client.post('/maas/v1/tasks',headers=headers(idem='rename-'+name),json=body).status_code==200
    assert s.metrics()['successful_tasks']==2
