"""Concurrency failure must not dispatch a prequeued remainder of the workload."""
from pathlib import Path
import sys
import threading
import pytest

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'scripts/maas/runtime'))
from benchmark_executors import bounded_map


def test_one_client_failure_does_not_call_later_items():
    calls=[]
    def work(n):
        calls.append(n)
        raise RuntimeError('failed')
    with pytest.raises(RuntimeError,match='failed'):bounded_map(work,range(50),1)
    assert calls==[0]


def test_failure_drains_only_already_inflight_requests():
    calls=[];barrier=threading.Barrier(4);stop=threading.Event();finished=[]
    def work(n):
        calls.append(n);barrier.wait(timeout=3)
        if n==0:
            stop.set();raise RuntimeError('failed')
        stop.wait(3);finished.append(n)
        return n
    with pytest.raises(RuntimeError,match='failed'):bounded_map(work,range(50),4,stop)
    assert sorted(calls)==[0,1,2,3] and sorted(finished)==[1,2,3]


def test_results_keep_input_order_and_calls_are_bounded():
    active=0;peak=0;lock=threading.Lock()
    def work(n):
        nonlocal active,peak
        with lock:active+=1;peak=max(active,peak)
        try:return n*n
        finally:
            with lock:active-=1
    assert bounded_map(work,range(20),3)==[n*n for n in range(20)]
    assert peak<=3


def test_preexisting_stop_never_calls_a_request():
    stop=threading.Event();stop.set()
    with pytest.raises(RuntimeError,match='halted'):
        bounded_map(lambda _:pytest.fail('dispatched after stop'),range(10),4,stop)
