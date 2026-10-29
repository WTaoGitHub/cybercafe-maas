import threading
import time

import httpx
import pytest

from maas_bench.backend import TaskBackend
from maas_bench.contracts import Parameters
from maas_bench.saturation import InvalidSupply, RunPolicy, SaturationRunner, SupplyMonitor, watermarks


P=Parameters(4,4,4,4,100,100)


def activity(**kw):
    return dict(ready=8,global_queued=8,project_queued=8,running=4,sync_running=0,
        project_running=4,project_sync_running=0,unknown=0,sync_unknown=0,failed=0,
        successes=0,generation=3,paused=False,db_at=time.monotonic(),**kw)


def test_watermarks_preserve_queue_headroom():
    assert watermarks(P)==(8,16)
    assert watermarks(Parameters(8,8,8,8,10,10))==(8,9)
    with pytest.raises(ValueError):watermarks(P,5,100)
    with pytest.raises(ValueError):watermarks(Parameters(1,1,1,1,2,2))


@pytest.mark.parametrize('change,status',[
    ({'ready':0},'INVALID_UNDERSUPPLIED'),
    ({'unknown':1},None),({'sync_unknown':1},None),({'paused':True},None),
    ({'generation':4},None),({'sync_running':1},None),({'project_queued':101},None),
    ({'failed':1},None),
])
def test_monitor_stops_on_supply_and_safety_failures(change,status):
    monitor=SupplyMonitor(P,3,.25);row=activity();row.update(change)
    with pytest.raises(RuntimeError) as exc:monitor.observe(row,1,1.01,True)
    if status:assert exc.value.status==status


def test_sampling_gap_is_inconclusive_not_maximum_throughput():
    m=SupplyMonitor(P,3,.25);m.observe(activity(),1,1.01,True)
    with pytest.raises(InvalidSupply) as exc:m.observe(activity(),1.3,1.31,True)
    assert exc.value.status=='INCONCLUSIVE_SUPPLY_EVIDENCE'
    assert m.evidence()['continuous_occupancy_proven'] is False


class FakeBackend:
    def __init__(self):
        self.lock=threading.Lock();self.tasks={};self.active={};self.done=set();self.maximum_queue=0
        self.halt=threading.Event();self.fail_at=None;self.snapshots=0;self.starve=False;self.gap=False
        self.runner_thread=threading.Thread(target=self.work);self.runner_thread.start()

    def close(self):self.halt.set();self.runner_thread.join(2)

    def work(self):
        while not self.halt.wait(.002):
            with self.lock:
                for ident,until in tuple(self.active.items()):
                    if time.monotonic()>=until:self.done.add(ident);del self.active[ident]
                available=[i for i in self.tasks if i not in self.done and i not in self.active]
                for ident in available[:4-len(self.active)]:self.active[ident]=time.monotonic()+.035

    def snapshot(self,include_ids=False):
        self.snapshots+=1
        if self.gap and self.snapshots==12:time.sleep(.12)
        with self.lock:
            queued=len(self.tasks)-len(self.active)-len(self.done)
            self.maximum_queue=max(self.maximum_queue,queued)
            row=activity();row.update(ready=queued,global_queued=queued,project_queued=queued,
                running=len(self.active),project_running=len(self.active),successes=len(self.done),
                success_ids=sorted(self.done) if include_ids else [])
            if self.starve and self.snapshots>12:row['ready']=0
            return row

    def submit(self,sequence):
        if sequence==self.fail_at:raise TimeoutError('uncertain acceptance')
        with self.lock:
            ident='task_'+str(sequence);assert ident not in self.tasks;self.tasks[ident]=sequence;return ident

    def reconcile(self,task_ids):
        with self.lock:return [dict(id=i,state='SUCCEEDED' if i in self.done else 'RUNNING',attempts=1,events=1) for i in task_ids]


def run_fake(**kw):
    backend=FakeBackend();events=[]
    for name,value in kw.items():setattr(backend,name,value)
    try:
        runner=SaturationRunner(backend,P,RunPolicy(seconds=.2,min_successes=8,max_seconds=1,
            sample_seconds=.01,max_sample_gap=.08,prefill_seconds=2,drain_seconds=2),3,events.append)
        result=runner.run()
        return result,events,backend.maximum_queue
    finally:backend.close()


def test_continuous_supply_has_separate_committed_window_and_tail():
    result,events,maximum=run_fake()
    assert result['state']=='COMPLETE'
    assert result['supply']['ready_queue_min']>0 and result['supply']['sample_count']>=10
    assert result['measured_successes']>=8 and result['measurement_seconds']>=.2
    assert result['tail_completions']>0
    assert result['measured_successes']<len(result['accepted_task_ids'])
    assert maximum<100
    assert len({e['sequence'] for e in events if e['kind']=='intent'})==result['submitted_intents']


@pytest.mark.parametrize('kw,status',[
    ({'starve':True},'INVALID_UNDERSUPPLIED'),
    ({'gap':True},'INCONCLUSIVE_SUPPLY_EVIDENCE'),
    ({'fail_at':20},'INTERRUPTED_REQUIRES_INSPECTION'),
])
def test_stop_keeps_original_invalid_run_and_does_not_retry(kw,status):
    result,events,_=run_fake(**kw)
    assert result['state']==status
    if 'fail_at' in kw:
        assert sum(e['kind']=='intent' and e['sequence']==20 for e in events)==1
        assert max(e['sequence'] for e in events if e['kind']=='intent')<=23
    assert len([e for e in events if e['kind']=='summary'])==1


def test_http_adapter_preserves_intent_namespace_without_retries_or_polling():
    class Observer:
        scope=('a','e3-test12345:','mock.echo','1')
        def fresh(self):pass
    requests=[]
    def upstream(request):
        requests.append(request)
        if request.method=='GET':return httpx.Response(200,json={'revision':'1'})
        return httpx.Response(202,json={'id':'task_1','model':'mock.echo','revision':'1'})
    with httpx.Client(base_url='http://test',transport=httpx.MockTransport(upstream)) as client:
        backend=TaskBackend(client,Observer(),[{'input':{'text':'x'}}]);backend.prepare()
        assert backend.submit(0)=='task_1'
    assert [r.method for r in requests]==['GET','POST']
    assert requests[1].headers['Idempotency-Key']=='e3-test12345:0'
    import json
    assert json.loads(requests[1].content)['max_attempts']==1


def test_http_idempotent_reuse_is_not_a_new_acceptance():
    class Observer:scope=('a','e3-test12345:','mock.echo','1')
    with httpx.Client(base_url='http://test',transport=httpx.MockTransport(lambda _:httpx.Response(200,json={'id':'old'}))) as client:
        with pytest.raises(RuntimeError):TaskBackend(client,Observer(),[{'input':{'text':'x'}}]).submit(0)


def test_ocr_nested_artifact_is_checked_before_measurement():
    import hashlib
    class Observer:
        scope=('a','e3-test12345:','ocr.default','1')
        def fresh(self):pass
    seen=[]
    def upstream(request):
        seen.append(request.url.path)
        return httpx.Response(200,content=b'image-bytes') if request.url.path.endswith('/art_example') else httpx.Response(200,json={'revision':'1'})
    fixture={'input':{'image':{'artifact_id':'art_example'}},'sha256':hashlib.sha256(b'image-bytes').hexdigest()}
    with httpx.Client(base_url='http://test',transport=httpx.MockTransport(upstream)) as client:
        backend=TaskBackend(client,Observer(),[fixture]);backend.prepare()
        assert seen==['/maas/v1/models/ocr.default/capabilities','/maas/v1/artifacts/art_example']
        fixture['sha256']='bad'
        with pytest.raises(RuntimeError,match='checksum'):backend.prepare()


def test_result_download_rejects_wrong_revision_even_when_http_succeeded():
    class Observer:scope=('a','e3-test12345:','ocr.default','1')
    result={'outputs':{'lines':[]},'model_revision':'1','attempt':1,'usage':{'unit':'task','quantity':1}}
    with httpx.Client(base_url='http://test',transport=httpx.MockTransport(lambda _:httpx.Response(200,json=result))) as client:
        backend=TaskBackend(client,Observer(),[{'input':{'text':'x'}}])
        rows=[{'result_artifact_id':'art_example'}]
        assert backend.verify_results(rows)['downloaded_results']==1
        result['model_revision']='changed'
        with pytest.raises(RuntimeError,match='mismatch'):backend.verify_results(rows)


def test_async_quality_matches_the_image_associated_with_its_sequence():
    import hashlib
    class Observer:scope=('a','e3-test12345:','ocr.default','1')
    result={'outputs':{'lines':[{'text':'first'}]},'model_revision':'1','attempt':1,'usage':{'unit':'task','quantity':1}}
    fixtures=[{'input':{'text':'x'},'expected_text_sha256':hashlib.sha256(t.encode()).hexdigest()} for t in ('first','second')]
    with httpx.Client(base_url='http://test',transport=httpx.MockTransport(lambda _:httpx.Response(200,json=result))) as client:
        backend=TaskBackend(client,Observer(),fixtures)
        assert backend.verify_results([{'result_artifact_id':'art_example','idempotency_key':'e3-test12345:0'}])['downloaded_results']==1
        with pytest.raises(RuntimeError,match='fixed reference'):
            backend.verify_results([{'result_artifact_id':'art_example','idempotency_key':'e3-test12345:1'}])


@pytest.mark.parametrize('kw',[{'seconds':float('nan')},{'sample_seconds':.2},{'min_successes':20,'max_submissions':10}])
def test_policy_rejects_unbounded_or_unmeasurable_parameters(kw):
    with pytest.raises(ValueError):RunPolicy(**kw)
