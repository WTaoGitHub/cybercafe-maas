import threading
from types import SimpleNamespace
from maas_platform.worker import Worker


class Service:
    def __init__(self,event):
        self.event=event;self.claimed=0;self.recovered=0;self.deferred=[]
    def recover(self):self.recovered+=1
    def claim(self):self.claimed+=1;return {'id':'task_test','attempts':1}
    def defer_dispatch(self,task,reason):self.deferred.append((task['id'],reason));return True


def test_signal_before_claim_does_no_db_work():
    stop=threading.Event();stop.set();s=Service(stop)
    assert not Worker(s,stop_claiming=stop).run_once()
    assert s.recovered==s.claimed==0


def test_signal_during_recovery_does_not_claim():
    stop=threading.Event();s=Service(stop);s.recover=stop.set
    assert not Worker(s,stop_claiming=stop).run_once() and s.claimed==0


def test_signal_racing_with_claim_defers_only_unsent_attempt():
    stop=threading.Event();s=Service(stop);old=s.claim
    def claim():stop.set();return old()
    s.claim=claim
    assert Worker(s,executor_id='executor-test',stop_claiming=stop).run_once()
    assert s.deferred==[('task_test','EXECUTOR_DRAINING')]


def test_process_identity_is_stable_for_a_worker_and_distinct_between_workers():
    a=Worker(None);b=Worker(None)
    assert a.executor_id==a.executor_id and a.executor_id!=b.executor_id
