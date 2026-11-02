import asyncio,json,threading,multiprocessing
from contextlib import ExitStack
from concurrent.futures import ThreadPoolExecutor
import pytest
from maas_platform.errors import ServiceError
from maas_platform.task_image_url import download_slot
from maas_platform.worker import Worker
from test_integration import env,headers
from test_inline_tasks_integration import setup,counts
from test_consumer_ocr import png

URL='https://example.org/photo.png?signature=test-placeholder'
def post(client,idem='url-one',url=URL,**change):
    body={'model':'ocr.default','input':{'image':{'image_url':url}},'max_attempts':1};body.update(change)
    return client.post('/maas/v1/tasks',headers=headers(idem=idem),json=body)


def mock_fetch(monkeypatch,action=None):
    calls=[]
    async def fetch(self,url,limit):
        calls.append(url)
        if action:return action()
        return png()
    monkeypatch.setattr('maas_platform.task_image_url.PublicImageFetcher.fetch',fetch)
    return calls


def test_remote_input_persisted_idempotency_no_redownload_and_result(env,monkeypatch):
    client,s,_=setup(env,monkeypatch);calls=mock_fetch(monkeypatch)
    r=post(client);assert r.status_code==202 and counts(s)==[1,1]
    assert post(client).status_code==200 and calls==[URL]
    assert post(client,url='https://example.org/changed.png').status_code==409 and calls==[URL]
    with s.db.transaction() as c:
        row=c.execute('SELECT input,input_artifact_id FROM tasks').fetchone()
        assert row['input']=={'image':{'artifact_id':row['input_artifact_id']}} and 'signature' not in json.dumps(row)
    assert Worker(s).run_once()
    task=client.get('/maas/v1/tasks/'+r.json()['id'],headers=headers()).json();assert task['state']=='SUCCEEDED'
    assert client.get('/maas/v1/artifacts/'+task['result_artifact_id'],headers=headers()).json()['outputs']['text']=='hello'
    assert s.metrics()['successful_tasks']==1 and post(client).json()['state']=='SUCCEEDED' and calls==[URL]


@pytest.mark.parametrize('kind,status',[('private',422),('format',422),('failed',422),('timeout',408),('size',413),('queue',429),('quota',429),('forbidden',403)])
def test_rejection_before_or_after_fetch_no_files(env,monkeypatch,kind,status):
    client,s,settings=setup(env,monkeypatch);url=URL
    def action():
        if kind=='failed':raise ServiceError('IMAGE_FETCH_FAILED','failure',422)
        if kind=='timeout':raise ServiceError('IMAGE_FETCH_TIMEOUT','timeout',408)
        if kind=='format':return b'invalid'
        if kind=='size':settings.max_input_bytes=1
        if kind=='quota':settings.max_project_storage_bytes=1
        return png()
    calls=mock_fetch(monkeypatch,action)
    if kind=='private':url='http://127.0.0.1/image'
    if kind=='queue':settings.max_queued=0
    if kind=='forbidden':
        r=client.post('/maas/v1/tasks',headers=headers('b','url-forbidden'),json={'model':'ocr.default','input':{'image':{'image_url':URL}}})
    else:r=post(client,url=url)
    assert r.status_code==status and counts(s)==[0,0] and not list(s.store.root.glob('art_*'))
    if kind in ('private','queue','forbidden'):assert not calls


def test_same_key_in_progress_and_network_does_not_hold_global_or_file_lock(env,monkeypatch):
    client,s,settings=setup(env,monkeypatch);entered=threading.Event();release=threading.Event()
    def slow():entered.set();assert release.wait(5);return png()
    calls=mock_fetch(monkeypatch,slow)
    with ThreadPoolExecutor(max_workers=1) as pool:
        pending=pool.submit(post,client)
        try:
            assert entered.wait(5)
            r=post(client);assert r.status_code==409 and r.json()['error']['code']=='IDEMPOTENCY_IN_PROGRESS'
            # Separate API operations acquire maintenance/file locks while fetch is blocked.
            s.pause(True,'test network wait is not a maintenance lock')
            r=client.post('/maas/v1/artifacts',headers={**headers(),'Content-Type':'image/png'},content=png());assert r.status_code==201
            s.pause(False)
        finally:release.set()
        assert pending.result(timeout=10).status_code==202
    assert calls==[URL]


@pytest.mark.parametrize('drift,status',[('queue',429),('quota',429),('revoke',403),('model',409)])
def test_final_admission_rechecks_after_download(env,monkeypatch,drift,status):
    client,s,settings=setup(env,monkeypatch)
    def action():
        if drift=='queue':settings.max_queued=0
        if drift=='quota':settings.max_project_storage_bytes=1
        if drift=='revoke':
            b=json.loads(settings.bindings_file.read_text());b['credentials']=b['credentials'][1:];settings.bindings_file.write_text(json.dumps(b))
        if drift=='model':
            cat=json.loads(settings.catalog_file.read_text());cat['ocr.default']['revision']='changed';settings.catalog_file.write_text(json.dumps(cat))
        return png()
    mock_fetch(monkeypatch,action)
    assert post(client).status_code==status and counts(s)==[0,0] and not list(s.store.root.glob('art_*'))


def hold_child(url,ready,release):
    with download_slot(url,'cross-process','shared'):
        ready.set();assert release.wait(10)


def test_cross_process_idempotency_lock_and_release(env):
    _,s,*_=env;ctx=multiprocessing.get_context('spawn');ready=ctx.Event();release=ctx.Event();p=ctx.Process(target=hold_child,args=(s.db.url,ready,release));p.start()
    try:
        assert ready.wait(10)
        with pytest.raises(ServiceError) as exc:
            with download_slot(s.db.url,'cross-process','shared'):pass
        assert exc.value.code=='IDEMPOTENCY_IN_PROGRESS'
    finally:release.set();p.join(10)
    assert p.exitcode==0
    with download_slot(s.db.url,'cross-process','shared'):pass


def test_project_and_global_download_slots(env):
    _,s,*_=env
    with ExitStack() as stack:
        for n in range(2):stack.enter_context(download_slot(s.db.url,'project',str(n)))
        with pytest.raises(ServiceError) as exc:
            with download_slot(s.db.url,'project','third'):pass
        assert exc.value.code=='IMAGE_FETCH_CAPACITY'
    with ExitStack() as stack:
        for n in range(4):stack.enter_context(download_slot(s.db.url,'project-'+str(n),str(n)))
        with pytest.raises(ServiceError) as exc:
            with download_slot(s.db.url,'fifth','key'):pass
        assert exc.value.code=='IMAGE_FETCH_CAPACITY'
    with download_slot(s.db.url,'fifth','key'):pass
