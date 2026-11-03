import base64,json
import httpx,pytest
from test_integration import env,headers,sync_setup
from test_consumer_ocr import png,response


def body(**kw):return {'model':'ocr.default','image_base64':base64.b64encode(png()).decode(),**kw}

@pytest.mark.parametrize('path',['/ocr'])
def test_consumer_paths_share_admission_and_map_before_single_success_event(env,monkeypatch,path):
    calls=[];box=[[0,0],[2,0],[2,2],[0,2]]
    def upstream(request):
        calls.append(request);sent=json.loads(request.content)
        assert sent['model']=='real-ocr' and 'request_id' not in sent and 'return_boxes' not in sent
        return httpx.Response(200,content=response([{'text':'中文 English','score':.9,'box':box}]))
    client,s,_=sync_setup(env,monkeypatch,upstream)
    r=client.post(path,headers=headers(),json=body(language='zh',request_id='consumer-001'))
    assert r.status_code==200 and r.json()=={'lines':[{'text':'中文 English','confidence':.9,'polygon':box}]}
    assert r.headers['x-request-id'].startswith('req_') and 'language' not in r.json()
    with s.db.transaction() as c:
        assert c.execute('SELECT count(*) n FROM sync_usage_events').fetchone()['n']==1
        assert c.execute('SELECT state,outcome FROM sync_activity').fetchone()=={'state':'COMPLETED','outcome':'SUCCEEDED'}
    assert len(calls)==1

@pytest.mark.parametrize('kind',['empty','no-box','invalid-output'])
def test_empty_result_and_output_failure_metering(env,monkeypatch,kind):
    lines=[] if kind=='empty' else [{'text':'x','score':.5,'box':[[0,0],[2,0],[2,2],[0,2]]}]
    def upstream(request):return httpx.Response(200,content=b'{}' if kind=='invalid-output' else response(lines))
    client,s,_=sync_setup(env,monkeypatch,upstream)
    r=client.post('/ocr',headers=headers(),json=body(return_boxes=False))
    assert r.status_code==(502 if kind=='invalid-output' else 200)
    if kind!='invalid-output':assert all('polygon' not in line for line in r.json()['lines'])
    with s.db.transaction() as c:
        assert c.execute('SELECT count(*) n FROM sync_usage_events').fetchone()['n']==(0 if kind=='invalid-output' else 1)
        assert c.execute('SELECT state FROM sync_activity').fetchone()['state']=='COMPLETED'

@pytest.mark.parametrize('kind',['base64','private-url','schema'])
def test_invalid_input_never_reaches_inference_or_success_metering(env,monkeypatch,kind):
    calls=[]
    def upstream(request):calls.append(request);return httpx.Response(200,content=response([]))
    client,s,_=sync_setup(env,monkeypatch,upstream)
    value=body(image_base64='bad!')
    if kind=='private-url':value={'model':'ocr.default','image_url':'http://169.254.169.254/latest/meta-data/'}
    if kind=='schema':value=body(language='unsupported')
    r=client.post('/ocr',headers=headers(),json=value);assert r.status_code==422
    assert not calls
    with s.db.transaction() as c:
        assert c.execute('SELECT count(*) n FROM sync_usage_events').fetchone()['n']==0
        assert c.execute("SELECT count(*) n FROM sync_activity WHERE state='UNKNOWN'").fetchone()['n']==0


def test_url_input_is_downloaded_only_after_authorization_maintenance_and_capacity(env,monkeypatch):
    client,s,_=sync_setup(env,monkeypatch,lambda request:httpx.Response(200,content=response([])))
    downloads=[]
    async def fetch(self,url,limit):
        with s.db.transaction() as c:assert c.execute("SELECT count(*) n FROM sync_activity WHERE state='DISPATCHING'").fetchone()['n']==1
        downloads.append(url);return png()
    monkeypatch.setattr('maas_platform.image_fetch.PublicImageFetcher.fetch',fetch)
    value={'model':'ocr.default','image_url':'https://example.org/a.png'}
    assert client.post('/ocr',headers=headers('b'),json=value).status_code==403
    s.pause(True);assert client.post('/ocr',headers=headers(),json=value).status_code==503;s.pause(False)
    ingress=client.app.state.sync_ingress;who=s.auth.authenticate('gpustack_registered_a');token=ingress.begin(who,'ocr.default',5)
    assert client.post('/ocr',headers=headers(),json=value).status_code==429
    ingress.finish(token,True)
    assert not downloads
    assert client.post('/ocr',headers=headers(),json=value).status_code==200
    assert downloads==['https://example.org/a.png']


def test_transport_unknown_blocks_following_requests_without_replay(env,monkeypatch):
    calls=[]
    def fail(request):calls.append(request);raise httpx.ReadError('unknown remote completion')
    client,s,_=sync_setup(env,monkeypatch,fail)
    assert client.post('/ocr',headers=headers(),json=body()).status_code==503
    assert client.post('/ocr',headers=headers(),json=body()).status_code==503
    assert len(calls)==1
    with s.db.transaction() as c:
        assert c.execute('SELECT state FROM sync_activity').fetchone()['state']=='UNKNOWN'
        assert c.execute('SELECT count(*) n FROM sync_usage_events').fetchone()['n']==0


@pytest.mark.parametrize('field,value', [
    ('image_base64', base64.b64encode(png()).decode()),
    ('image_url', 'https://example.org/image.png'),
])
def test_v1_ocr_shares_task_submission_and_idempotency(env, monkeypatch, field, value):
    client, service, _ = sync_setup(env, monkeypatch, lambda _: pytest.fail('Submission must not run OCR'))
    async def fetch(*args):
        return png()
    monkeypatch.setattr('maas_platform.task_image_url.fetch_image', fetch)
    payload = {'model': 'ocr.default', field: value}
    key = headers(idem='ocr-alias')
    assert client.post('/v1/ocr', headers=headers(), json=payload).status_code == 400
    created = client.post('/v1/ocr', headers=key, json=payload)
    assert created.status_code == 202
    assert created.headers['location'] == '/maas/v1/tasks/' + created.json()['id']
    replay = client.post('/maas/v1/tasks', headers=key, json={
        'model': 'ocr.default', 'input': {'image': {field: value}},
    })
    assert replay.status_code == 200 and replay.json() == created.json()
    with service.db.transaction() as conn:
        assert conn.execute('SELECT count(*) n FROM tasks').fetchone()['n'] == 1


@pytest.mark.parametrize('return_boxes', [False, True])
def test_v1_ocr_return_boxes_applies_to_downloaded_result(env, monkeypatch, return_boxes):
    from maas_platform.worker import Worker
    from test_metering_integration import configured

    client, service, _ = configured(env, monkeypatch)
    payload = body(return_boxes=return_boxes)
    key = headers(idem='ocr-result-shape')
    created = client.post('/v1/ocr', headers=key, json=payload)
    assert created.status_code == 202
    assert client.post('/v1/ocr', headers=key, json={
        **payload, 'return_boxes': not return_boxes,
    }).status_code == 409
    replay = client.post('/maas/v1/tasks', headers=key, json={
        'model': payload['model'],
        'input': {'image': {'image_base64': payload['image_base64']}},
        'parameters': {} if return_boxes else {'return_boxes': False},
    })
    assert replay.status_code == 200
    assert Worker(service).run_once()
    task = client.get(created.headers['location'], headers=headers()).json()
    assert task['state'] == 'SUCCEEDED'
    response = client.get('/maas/v1/artifacts/' + task['result_artifact_id'], headers=headers())
    assert response.status_code == 200
    line, = response.json()['outputs']['lines']
    assert line['text'] == 'hello'
    assert line['score'] == 0.9
    assert ('box' in line) == return_boxes
