"""Real disposable PostgreSQL, real API validation, explicitly mocked inference."""
import hashlib
import json
from pathlib import Path
import threading
import time
import httpx
import pytest
from test_integration import env,headers,sync_setup
from test_consumer_ocr import png,response
from maas_bench.backend import DatabaseObserver,TaskBackend
from maas_bench.contracts import Parameters
from maas_bench.saturation import SaturationRunner,RunPolicy
from maas_bench.sync_load import SyncTraffic
from maas_platform.worker import Worker


def setup(env,monkeypatch,tmp_path):
    lines=[{'text':'hello','score':.9,'box':[[0,0],[2,0],[2,2],[0,2]]}]
    client,s,settings=sync_setup(env,monkeypatch,lambda req:httpx.Response(200,content=response(lines)))
    client.headers.update(headers())
    image=tmp_path/'input.png';image.write_bytes(png())
    manifest=tmp_path/'manifest.json';manifest.write_text(json.dumps([{'id':'one','file':image.name,'sha256':hashlib.sha256(image.read_bytes()).hexdigest()}]))
    quality=tmp_path/'quality.json';quality.write_text(json.dumps({'items':{'one':{'text_sha256':hashlib.sha256(b'hello').hexdigest()}}}))
    config={'sync_rate':2,'raw_manifest_file':str(manifest),'expected_text_file':str(quality),
        'logical_model':'ocr.default','base_url':'http://test','customer_key_file':str(settings.bindings_file.parent/'key-a'),'ca_file':True}
    obs=DatabaseObserver(settings.database_url,'a','ocr.default','ocr-r1','e3-t3-integration12345:',statement_ms=1000)
    return client,s,settings,config,obs,lines


def test_sync_traffic_uses_real_api_and_once_usage(env,monkeypatch,tmp_path):
    client,s,settings,cfg,obs,_=setup(env,monkeypatch,tmp_path)
    errors=[];traffic=SyncTraffic(cfg,obs,errors.append,lambda _:None);traffic.before={r['token'] for r in traffic._rows()}
    try:
        assert traffic._one(client,0,time.monotonic())['http_status']==200
        assert traffic.stop()['usage_events']==1 and not errors
    finally:obs.close()


def test_sync_capacity_rejection_creates_no_new_usage(env,monkeypatch,tmp_path):
    client,s,settings,cfg,obs,_=setup(env,monkeypatch,tmp_path)
    ingress=client.app.state.sync_ingress;token=ingress.begin(s.auth.authenticate('gpustack_registered_a'),'ocr.default',30)
    traffic=SyncTraffic(cfg,obs,lambda e:pytest.fail(type(e).__name__),lambda _:None);traffic.before={r['token'] for r in traffic._rows()}
    try:
        assert traffic._one(client,0,time.monotonic())['http_status']==429
        result=traffic.stop();assert result['usage_events']==0 and result['capacity_rejections']==1
        ingress.finish(token,True)
    finally:obs.close()


def test_mixed_saturated_queue_and_sync_usage_share_real_project_slots(env,monkeypatch,tmp_path):
    client,s,settings,cfg,obs,lines=setup(env,monkeypatch,tmp_path)
    settings.max_running=settings.max_project_running=4
    def fake_execute(self,ctx):
        time.sleep(.1)
        return {'text':'hello','lines':lines,'elapsed_ms':100,'node':'mock-only'}
    monkeypatch.setattr('maas_platform.ocr_connector.OCRAdapter.execute',fake_execute)
    artifact=client.post('/maas/v1/artifacts',content=png(),headers={'Content-Type':'image/png'}).json()['id']
    fixtures=[{'input':{'image':{'artifact_id':artifact}},'sha256':hashlib.sha256(png()).hexdigest(),
        'expected_text_sha256':hashlib.sha256(b'hello').hexdigest()}]
    backend=TaskBackend(client,obs,fixtures,allow_sync=True);backend.prepare()
    initial=obs.snapshot();runner=SaturationRunner(backend,Parameters(2,2,4,4,1000,100),
        RunPolicy(seconds=.4,min_successes=4,max_seconds=3,sample_seconds=.02,max_sample_gap=.5,prefill_seconds=5,drain_seconds=10),initial['generation'])
    traffic=SyncTraffic(cfg,obs,runner._fail,lambda _:None);backend.stop_auxiliary=traffic.stop
    original_client=httpx.Client
    def local_client(**kwargs):
        kwargs.pop('transport',None)
        return original_client(**kwargs,transport=httpx.MockTransport(lambda req:client.request(req.method,req.url.path,content=req.content,headers=dict(req.headers))))
    monkeypatch.setattr('maas_bench.sync_load.httpx.Client',local_client)
    halt=threading.Event();workers=[]
    def work():
        worker=Worker(s,stop_claiming=halt)
        while not halt.is_set():
            if not worker.run_once():halt.wait(.003)
    try:
        for _ in range(2):
            t=threading.Thread(target=work);t.start();workers.append(t)
        traffic.start();result=runner.run()
        assert result['state']=='COMPLETE',result
        assert result['auxiliary']['successes']>=1 and result['auxiliary']['usage_events']>=1
        assert result['supply']['ready_queue_min']>0
    finally:
        halt.set();traffic.halt.set()
        if traffic.thread.ident is not None:traffic.thread.join(10)
        for t in workers:t.join(10);assert not t.is_alive()
        obs.close()
