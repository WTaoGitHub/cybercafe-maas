"""Timed native/sync requests: independent bounded lanes with explicit tails."""
import math
import threading
import time
from .contracts import integer


def timed_prepared(invoke,inputs,clients,seconds=60,min_successes=200,max_seconds=300,
                   stop=None,max_calls=200000,max_refill_gap=.25):
    integer(clients,'clients');integer(min_successes,'min_successes',maximum=200000)
    integer(max_calls,'max_calls',maximum=200000)
    for value in (seconds,max_seconds,max_refill_gap):
        if isinstance(value,bool) or not isinstance(value,(int,float)) or not math.isfinite(value) or value<=0:
            raise ValueError('Finite positive measurement budgets required')
    if not inputs or max_seconds<seconds or max_calls<min_successes:raise ValueError('Inconsistent request budgets')
    stop=stop if stop is not None else threading.Event()
    finished=threading.Event()
    gate=threading.Event();lock=threading.Lock();ready=threading.Condition(lock)
    records=[];failures=[];sequence=0;ready_count=0;started=None
    def lane(number):
        nonlocal sequence,ready_count
        with ready:ready_count+=1;ready.notify_all()
        gate.wait();previous=None
        while not stop.is_set() and not finished.is_set():
            with lock:
                if stop.is_set() or finished.is_set():break
                if sequence>=max_calls:
                    failures.append('SUBMISSION_BUDGET');stop.set();break
                seq=sequence;sequence+=1
            begin=time.monotonic()
            try:
                outcome=invoke(inputs[seq%len(inputs)],seq)
                completed=time.monotonic()
                with lock:records.append({'sequence':seq,'lane':number,'sent_offset':begin-started,
                    'completed_offset':completed-started,'seconds':completed-begin,
                    'refill_gap_seconds':max(0,begin-previous) if previous is not None else begin-started,
                    'outcome':outcome})
                previous=completed
            except Exception as exc:
                with lock:failures.append(type(exc).__name__)
                stop.set();break
    threads=[threading.Thread(target=lane,args=(i,),name='prepared-'+str(i)) for i in range(clients)]
    for t in threads:t.start()
    with ready:
        if not ready.wait_for(lambda:ready_count==clients,timeout=10):
            stop.set();gate.set();raise RuntimeError('Client lanes did not become ready')
    started=time.monotonic();gate.set();state='COMPLETE'
    try:
        while not finished.wait(.002):
            if stop.is_set():state='INTERRUPTED_REQUIRES_INSPECTION';break
            elapsed=time.monotonic()-started
            with lock:completed=len(records)
            if elapsed>=seconds and completed>=min_successes:break
            if elapsed>=max_seconds:state='INCONCLUSIVE_INSUFFICIENT_COMPLETIONS';break
    except BaseException:
        state='INTERRUPTED_REQUIRES_INSPECTION';raise
    finally:
        cut=time.monotonic();finished.set()
        # invoke must use a bounded transport timeout (native driver: 35s).
        for t in threads:t.join(timeout=40)
    if any(t.is_alive() for t in threads):raise RuntimeError('Unfinished in-flight request; inspect without replay')
    window=cut-started;measured=[r for r in records if r['completed_offset']<=window]
    gap=max((r['refill_gap_seconds'] for r in records if r['sent_offset']<=window),default=0)
    if failures:state='INTERRUPTED_REQUIRES_INSPECTION'
    elif stop.is_set() or (window<seconds or len(measured)<min_successes) and state=='COMPLETE':state='INTERRUPTED_REQUIRES_INSPECTION'
    if state=='COMPLETE' and gap>max_refill_gap:state='INCONCLUSIVE_SUPPLY_EVIDENCE'
    return {'state':state,'mode':'timed_prepared_native_or_sync','clients':clients,
        'measurement_seconds':window,'measured_successes':len(measured),'tail_completions':len(records)-len(measured),
        'throughput_per_second':len(measured)/window,'scheduled_calls':sequence,
        'maximum_refill_gap_seconds':gap,'continuous_supply_proven':False,
        'errors':failures,'records':records}
