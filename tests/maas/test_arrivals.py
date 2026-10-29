import threading
import time
from types import SimpleNamespace
import httpx
import pytest
from maas_bench.arrivals import drive,AdmissionBackend


def test_arrival_clock_does_not_wait_for_business_completion():
    result=drive(lambda n:time.sleep(.03),clients=2,rate=30,seconds=.2)
    assert result['state']=='COMPLETE' and len(result['records'])==6
    assert result['measurement_seconds']>=.2


def test_busy_arrivals_are_unsent_not_queued_or_retried():
    result=drive(lambda n:time.sleep(.05),clients=1,rate=200,seconds=.1)
    assert result['state']=='INCONCLUSIVE_GENERATOR' and result['unsent']
    assert len({r['sequence'] for r in result['records']})==len(result['records'])


def test_burst_is_bounded_and_finishes_every_sent_call():
    active=peak=0;lock=threading.Lock()
    def invoke(n):
        nonlocal active,peak
        with lock:active+=1;peak=max(active,peak)
        time.sleep(.005)
        with lock:active-=1
        return n
    result=drive(invoke,clients=4,count=25)
    assert result['state']=='COMPLETE' and peak<=4 and len(result['records'])==25


def test_external_failure_stops_new_dispatch_without_replay():
    calls=[]
    def invoke(n):calls.append(n);raise RuntimeError('uncertain')
    result=drive(invoke,clients=1,count=20)
    assert result['state']=='INTERRUPTED_REQUIRES_INSPECTION' and calls==[0]


@pytest.mark.parametrize('kw',[{'clients':17,'count':1},{'clients':1,'count':100001},{'clients':1,'rate':float('inf'),'seconds':10}])
def test_invalid_plan_does_not_dispatch(kw):
    with pytest.raises(ValueError):drive(lambda _:pytest.fail('dispatched'),**kw)


def test_expected_queue_full_does_not_become_accepted_or_retried():
    calls=[]
    def request(req):calls.append(req);return httpx.Response(429,json={'error':{'code':'QUEUE_FULL'}})
    b=SimpleNamespace(observer=SimpleNamespace(scope=('p','e3-arrival-test:','ocr.default','r1')),
        fixtures=[{'input':{'image':{'artifact_id':'one'}}}],timeout=120,
        client=httpx.Client(base_url='http://test',transport=httpx.MockTransport(request)))
    a=AdmissionBackend(b);assert a.submit(0)['http_status']==429
    assert len(calls)==1 and not a.accepted and len(a.rejected)==1
