import threading
import time
import pytest
from maas_bench.prepared import timed_prepared


def test_timed_lanes_measure_successes_without_tail_in_numerator():
    shared_abort=threading.Event()
    def invoke(item,seq):time.sleep(.02);return {'ok':True}
    value=timed_prepared(invoke,['prepared'],6,seconds=.1,min_successes=12,max_seconds=.3,stop=shared_abort)
    assert value['state']=='COMPLETE' and value['measurement_seconds']>=.1
    assert value['measured_successes']>=12 and value['tail_completions']>0
    assert len(value['records'])==value['scheduled_calls']
    assert value['throughput_per_second']==value['measured_successes']/value['measurement_seconds']
    assert not shared_abort.is_set()


def test_small_sample_extends_whole_window_instead_of_restarting_clock():
    def invoke(item,seq):time.sleep(.02);return 1
    value=timed_prepared(invoke,[1],1,seconds=.01,min_successes=4,max_seconds=.3)
    assert value['state']=='COMPLETE' and value['measurement_seconds']>=.08


def test_insufficient_sample_is_not_a_capacity_pass():
    def invoke(item,seq):time.sleep(.02);return 1
    value=timed_prepared(invoke,[1],1,seconds=.01,min_successes=100,max_seconds=.06)
    assert value['state']=='INCONCLUSIVE_INSUFFICIENT_COMPLETIONS'


def test_failed_request_stops_lanes_and_is_never_replayed():
    calls=[];gate=threading.Barrier(4)
    def invoke(item,seq):
        calls.append(seq);gate.wait(2)
        if seq==0:raise TimeoutError()
        time.sleep(.02);return 1
    value=timed_prepared(invoke,[1],4,seconds=.1,min_successes=1,max_seconds=.2)
    assert value['state']=='INTERRUPTED_REQUIRES_INSPECTION' and sorted(calls)==[0,1,2,3]


@pytest.mark.parametrize('kw',[{'clients':17},{'seconds':float('nan')},{'max_seconds':.01,'seconds':1}])
def test_unsafe_budgets_fail_before_invoke(kw):
    with pytest.raises(ValueError):timed_prepared(lambda *_:pytest.fail('sent'),[1],**({'clients':4}|kw))
