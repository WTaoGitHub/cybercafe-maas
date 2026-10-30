"""History identity scans must not amplify usage reconciliation cost."""
from types import SimpleNamespace
import threading
import pytest
from maas_bench.sync_load import SyncTraffic


def traffic(history, new, events=1, missing=False):
    calls=[]
    def execute(sql, params):
        calls.append((sql,params))
        if 'sync_usage_events' not in sql:
            rows=[{'token':x} for x in history+new]
        else:
            rows=[{'token':x,'state':'COMPLETED','outcome':'SUCCEEDED','events':events}
                  for x in params[0] if not missing]
        return SimpleNamespace(fetchall=lambda:rows)
    t=object.__new__(SyncTraffic)
    t.observer=SimpleNamespace(scope=('a','unused','ocr.default','r1'),lock=threading.Lock(),
                               connection=SimpleNamespace(execute=execute))
    t.before=set(history);t.halt=threading.Event();t.thread=threading.Thread();t.failure=None
    t.records=[{'http_status':200} for _ in new];t.unsent=[];t.rate=1;t.clients=16
    return t,calls


def test_only_new_tokens_have_usage_queried_and_are_batched():
    t,calls=traffic([str(i) for i in range(15000)],['new'+str(i) for i in range(513)])
    assert t.stop()['usage_events']==513
    assert 'sync_usage_events' not in calls[0][0]
    assert [len(p[0]) for sql,p in calls[1:]]==[256,256,1]
    assert all(x.startswith('new') for sql,p in calls[1:] for x in p[0])
    assert all(p[-2:]==('a','ocr.default') for sql,p in calls[1:])


def test_rejection_only_does_not_query_usage():
    t,calls=traffic(['old'],[]);t.records=[{'http_status':429}]
    assert t.stop()['capacity_rejections']==1 and len(calls)==1


@pytest.mark.parametrize('events',[0,2])
def test_missing_or_duplicate_usage_still_fails(events):
    t,_=traffic(['old'],['new'],events=events)
    with pytest.raises(RuntimeError,match='usage window mismatch'):t.stop()


def test_disappearing_token_is_not_silently_ignored():
    t,_=traffic([],['new'],missing=True)
    with pytest.raises(RuntimeError,match='token set changed'):t.stop()


def test_extra_activity_cannot_hide_behind_matching_usage():
    t,_=traffic([],['new']);t.records=[]
    with pytest.raises(RuntimeError,match='usage window mismatch'):t.stop()
