"""Admission and drain against real disposable PG/API; inference explicitly mocked."""
import hashlib
import time
import threading
from test_integration import env
from test_t3_integration import setup
from test_consumer_ocr import png
from maas_bench.arrivals import AdmissionBackend,drive
from maas_bench.backend import TaskBackend
from maas_platform.worker import Worker


def prepared(env,monkeypatch,tmp_path):
    client,s,settings,cfg,obs,lines=setup(env,monkeypatch,tmp_path)
    client.app.state.service.settings.max_project_queued=1
    def execute(self,ctx):return {'text':'hello','lines':lines,'elapsed_ms':1,'node':'mock-only'}
    monkeypatch.setattr('maas_platform.ocr_connector.OCRAdapter.execute',execute)
    art=client.post('/maas/v1/artifacts',content=png(),headers={'Content-Type':'image/png'}).json()['id']
    fixtures=[{'input':{'image':{'artifact_id':art}},'sha256':hashlib.sha256(png()).hexdigest(),'expected_text_sha256':hashlib.sha256(b'hello').hexdigest()}]
    backend=TaskBackend(client,obs,fixtures);backend.prepare()
    return client,s,settings,obs,backend


def test_full_queue_has_no_accepted_task_or_usage_for_rejections_then_recovers(env,monkeypatch,tmp_path):
    client,s,settings,obs,backend=prepared(env,monkeypatch,tmp_path)
    a=AdmissionBackend(backend)
    try:
        result=drive(a.submit,clients=4,count=12)
        assert result['state']=='COMPLETE'
        assert len(a.accepted)==1 and len(a.rejected)==11 and a.rejected_absent()==11
        assert Worker(s).run_once()
        rows=backend.reconcile(a.accepted);assert rows[0]['state']=='SUCCEEDED' and rows[0]['events']==1 and rows[0]['attempts']==1
        assert backend.verify_results(rows)['downloaded_results']==1
        assert a.submit(12)['http_status']==202
        assert Worker(s).run_once()
        assert all(r['events']==1 for r in backend.reconcile(a.accepted))
    finally:obs.close()


def test_open_arrivals_with_real_worker_preserve_once_completion_and_natural_empty_queue(env,monkeypatch,tmp_path):
    client,s,settings,obs,backend=prepared(env,monkeypatch,tmp_path)
    settings.max_project_queued=25;a=AdmissionBackend(backend);halt=threading.Event()
    def work():
        worker=Worker(s,stop_claiming=halt)
        while not halt.is_set():
            if not worker.run_once():halt.wait(.005)
    t=threading.Thread(target=work);t.start()
    try:
        result=drive(a.submit,clients=4,rate=5,seconds=1)
        assert result['state']=='COMPLETE' and not a.rejected
        until=time.monotonic()+10
        while obs.snapshot()['successes']<len(a.accepted) and time.monotonic()<until:time.sleep(.01)
        rows=backend.reconcile(a.accepted)
        assert len(rows)==5 and all(r['state']=='SUCCEEDED' and r['events']==1 and r['attempts']==1 for r in rows)
        assert backend.verify_results(rows)['downloaded_results']==5
        assert obs.snapshot()['ready']==0
    finally:halt.set();t.join(10);obs.close()
