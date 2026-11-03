import asyncio,base64,json
import httpx,pytest
from maas_platform.worker import Worker
from test_integration import env,headers
from test_inline_tasks_integration import setup,post
from test_consumer_ocr import png


def configured(env,monkeypatch,usage=None,status=200):
    factory=httpx.AsyncClient;client,s,settings=setup(env,monkeypatch)
    value={'text':'hello','lines':[{'text':'hello','score':.9,'box':[[0,0],[1,0],[1,1],[0,1]]}],'elapsed_ms':1,'node':'test'}
    envelope={'choices':[{'message':{'content':json.dumps(value)}}]}
    if usage is not None:envelope['usage']=usage
    def response(req):return httpx.Response(status,json=envelope)
    monkeypatch.setattr('maas_platform.ocr_connector.httpx.AsyncClient',lambda **kw:factory(transport=httpx.MockTransport(response),**kw))
    client.app.state.sync_ingress.client_factory=lambda **kw:factory(transport=httpx.MockTransport(response),**kw)
    return client,s,settings


def ledger(s):
    with s.db.transaction() as c:return c.execute('SELECT count(*) n FROM ocr_usage_records').fetchone()['n']


@pytest.mark.parametrize('usage,available',[(None,False),({'prompt_tokens':11,'completion_tokens':22,'total_tokens':33},True),({'prompt_tokens':0,'completion_tokens':0},True),({'prompt_tokens':1,'completion_tokens':-1},False)])
def test_async_images_tokens_and_queries_survive_file_expiry(env,monkeypatch,usage,available):
    client,s,_=configured(env,monkeypatch,usage);task=post(client).json();assert Worker(s).run_once()
    row=client.get('/maas/v1/tasks/'+task['id'],headers=headers()).json();assert row['state']=='SUCCEEDED'
    result=client.get('/maas/v1/artifacts/'+row['result_artifact_id'],headers=headers()).json()
    assert result['usage']=={'unit':'task','quantity':1}
    meter=result['metering'];assert meter['successful_images']==1 and meter['tokens']['status']==('available' if available else 'unavailable')
    assert meter['tokens']['input_tokens']==(usage['prompt_tokens'] if available else None)
    for path in ['/maas/v1/tasks/'+task['id']+'/usage','/maas/v1/usage/'+meter['id']]:
        reply=client.get(path,headers=headers());assert reply.json()==meter and reply.headers['cache-control']=='no-store'
        assert client.get(path,headers=headers('b')).status_code==404
    assert post(client).status_code==200 and ledger(s)==1 and not Worker(s).run_once()
    with s.db.transaction() as c:c.execute("UPDATE artifacts SET expires_at=now()-interval '1 second'")
    s.cleanup()
    assert client.get('/maas/v1/artifacts/'+row['result_artifact_id'],headers=headers()).status_code==410
    assert client.get('/maas/v1/usage/'+meter['id'],headers=headers()).json()==meter


@pytest.mark.parametrize('path',['/v1/chat/completions'])
def test_sync_header_and_persistent_usage_with_body_compatibility(env,monkeypatch,path):
    client,s,_=configured(env,monkeypatch,{'prompt_tokens':3,'completion_tokens':4})
    image=base64.b64encode(png()).decode();body={'model':'ocr.default','image_base64':image}
    if path.endswith('completions'):body={'model':'ocr.default','messages':[{'role':'user','content':[{'type':'image_url','image_url':{'url':'data:image/png;base64,'+image}}]}]}
    r=client.post(path,headers=headers(),json=body);assert r.status_code==200
    if path.endswith('ocr'):assert set(r.json())=={'lines'}
    meter=client.get('/maas/v1/usage/'+r.headers['X-Usage-ID'],headers=headers()).json()
    assert meter['successful_images']==1 and meter['tokens']['input_tokens']==3 and meter['tokens']['output_tokens']==4 and meter['revision']=='ocr-r1'
    assert ledger(s)==1
    with s.db.transaction() as c:assert c.execute('SELECT count(*) n FROM sync_usage_events').fetchone()['n']==1


@pytest.mark.parametrize('kind',['pending','cancel','failed','unknown','legacy'])
def test_no_final_usage_for_ineligible_or_historical_task(env,monkeypatch,kind):
    client,s,_=configured(env,monkeypatch,status=503 if kind=='unknown' else 403 if kind=='failed' else 200)
    task=post(client).json()
    if kind=='cancel':s.cancel(s.auth.authenticate('gpustack_registered_a'),task['id'])
    elif kind!='pending':
        assert Worker(s).run_once()
        if kind=='legacy':
            with s.db.transaction() as c:c.execute('DELETE FROM ocr_usage_records')
    r=client.get('/maas/v1/tasks/'+task['id']+'/usage',headers=headers());assert r.status_code==404 and r.json()['error']['code']=='USAGE_NOT_AVAILABLE' and ledger(s)==0


def test_probe_is_excluded(env,monkeypatch):
    client,s,_=configured(env,monkeypatch);s.pause(True)
    who=s.auth.authenticate('gpustack_registered_a');body={'model':'ocr.default','messages':[{'role':'user','content':[{'type':'image_url','image_url':{'url':'data:image/png;base64,'+base64.b64encode(png()).decode()}}]}]}
    r=asyncio.run(client.app.state.sync_ingress.infer(who,body,probe=True))
    assert r.status_code==200 and 'X-Usage-ID' not in r.headers and ledger(s)==0


def test_ledger_failure_rolls_back_success_and_metering(env,monkeypatch):
    client,s,_=configured(env,monkeypatch);task=post(client).json()
    def fail(*args):raise RuntimeError('ledger unavailable')
    monkeypatch.setattr('maas_platform.metering.persist',fail)
    assert Worker(s).run_once()
    assert ledger(s)==0
    with s.db.transaction() as c:
        assert c.execute('SELECT count(*) n FROM task_usage_events').fetchone()['n']==0
        assert c.execute('SELECT state FROM tasks WHERE id=%s',(task['id'],)).fetchone()['state']=='RUNNING'


def test_new_ledger_does_not_backfill_old_successes(env,monkeypatch):
    client,s,_=configured(env,monkeypatch);task=post(client).json();assert Worker(s).run_once()
    with s.db.transaction() as c:c.execute('DELETE FROM ocr_usage_records')
    s.db.migrate()
    assert ledger(s)==0
    with s.db.transaction() as c:
        assert c.execute('SELECT count(*) n FROM task_usage_events').fetchone()['n']==1
        assert c.execute('SELECT state FROM tasks WHERE id=%s',(task['id'],)).fetchone()['state']=='SUCCEEDED'
    assert client.get('/maas/v1/tasks/'+task['id']+'/usage',headers=headers()).status_code==404
