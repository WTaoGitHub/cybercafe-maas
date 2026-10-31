import threading
from types import SimpleNamespace
import pytest
from maas_bench.completion_witness import CompletionWitness


def test_witness_observes_commits_through_drain_and_checks_original_deadline():
    rows=[];errors=[];events=[];ready=threading.Event()
    def snapshot(include_ids=False):
        ready.set();return {'db_at':100,'completion_observed_db_at':101,'success_ids':list(rows)}
    o=SimpleNamespace(snapshot=snapshot,task_timings=lambda:[{'id':x,'created_epoch':99,'deadline_epoch':102} for x in rows])
    w=CompletionWitness(o,events.append,errors.append);w.start();assert ready.wait(1);rows.append('one');result=w.verify(['one'])
    assert result['all_accepted_observed_before_deadline'] and result['observed_completion_max']==2 and not errors and not w.thread.is_alive()
    assert events[-1]['kind']=='completion_witness_final'
    o.task_timings=lambda:[{'id':'one','created_epoch':99,'deadline_epoch':100.5}]
    with pytest.raises(RuntimeError,match='deadline'):w.verify(['one'])


def test_witness_query_failure_fails_closed_and_stops_finitely():
    errors=[]
    def snapshot(**_):raise RuntimeError('read unavailable')
    w=CompletionWitness(SimpleNamespace(snapshot=snapshot),lambda _:None,errors.append);w.start();w.thread.join(1)
    assert len(errors)==1 and not w.thread.is_alive()
    with pytest.raises(RuntimeError,match='unavailable'):w.verify([])
