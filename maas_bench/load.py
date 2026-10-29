"""Bounded prepared-request and independent-arrival drivers, without hidden retry."""
from concurrent.futures import ThreadPoolExecutor,wait,FIRST_COMPLETED
import math
import threading
import time

from .contracts import integer


def _budget(seconds):
    if isinstance(seconds,bool) or not isinstance(seconds,(int,float)) or not math.isfinite(seconds) or seconds<=0:
        raise ValueError('Positive finite duration required')


def prepared_requests(invoke,inputs,clients,seconds,max_calls=200000):
    """Closed-loop native/sync supply. Inputs prepared before timing begins.

    There are at most L calls in flight, no prequeued unbounded work. End-of-
    window completions and tail completions are recorded separately. Supply
    gaps are explicit; caller must assess them, not claim perfect continuity.
    """
    integer(clients,'clients');integer(max_calls,'max_calls',maximum=200000);_budget(seconds)
    if not inputs:raise ValueError('Prepared inputs required')
    stop=threading.Event();lock=threading.Lock();records=[];dispatched=0;last_end=None;gaps=[]
    started=time.monotonic();end=started+seconds;pending=set();error=None
    def call(n):
        nonlocal last_end
        if stop.is_set():return
        a=time.monotonic()
        with lock:
            if last_end is not None:gaps.append(max(0,a-last_end));last_end=None
        try:
            value=invoke(inputs[n%len(inputs)],n)
            record={'sequence':n,'sent_offset':a-started,'completed_offset':time.monotonic()-started,'outcome':value}
            with lock:records.append(record);last_end=time.monotonic()
        except Exception:
            stop.set();raise
    with ThreadPoolExecutor(max_workers=clients) as pool:
        try:
            while time.monotonic()<end and not stop.is_set():
                for f in tuple(pending):
                    if f.done():pending.remove(f);f.result()
                while len(pending)<clients and time.monotonic()<end and not stop.is_set():
                    if dispatched>=max_calls:raise RuntimeError('Bounded call budget exhausted')
                    pending.add(pool.submit(call,dispatched));dispatched+=1
                if pending:wait(pending,timeout=min(.01,max(0,end-time.monotonic())),return_when=FIRST_COMPLETED)
        except Exception as exc:error=type(exc).__name__;stop.set()
        finally:
            for f in pending:
                try:f.result()
                except Exception as exc:error=error or type(exc).__name__
    measured=[r for r in records if r['completed_offset']<=seconds]
    return {'state':'INTERRUPTED_REQUIRES_INSPECTION' if error else 'COMPLETE','error_class':error,
        'mode':'prepared_closed_loop','clients':clients,'measurement_seconds':seconds,
        'measured_completions':len(measured),'tail_completions':len(records)-len(measured),
        'throughput_per_second':len(measured)/seconds,'max_observed_refill_gap_seconds':max(gaps,default=0),
        'continuous_supply_proven':False,'records':records,'scheduled_calls':dispatched}


def independent_arrivals(invoke,rate,seconds,clients,max_lateness=.05,max_calls=200000):
    """An open-loop clock never speeds up/slows down to match completions.

    No waiting thread queue: busy/late slots are recorded unsent, not replayed
    later as a catch-up burst. Expected service rejection must be returned by
    the case adapter; exceptions stop all new scheduling.
    """
    integer(clients,'clients');integer(max_calls,'max_calls',maximum=200000)
    for n in (rate,seconds,max_lateness):_budget(n)
    target=math.ceil(rate*seconds)
    if target>max_calls:raise ValueError('Arrival plan exceeds bounded call budget')
    stop=threading.Event();lock=threading.Lock();records=[];unsent=[];pending=set();error=None
    started=time.monotonic()
    def call(n,scheduled):
        if stop.is_set():return
        actual=time.monotonic()
        try:
            value=invoke(n)
            with lock:records.append({'sequence':n,'scheduled_offset':scheduled-started,
                'sent_offset':actual-started,'lateness_seconds':actual-scheduled,
                'completed_offset':time.monotonic()-started,'outcome':value})
        except Exception:
            stop.set();raise
    with ThreadPoolExecutor(max_workers=clients) as pool:
        try:
            for n in range(target):
                scheduled=started+n/rate
                if stop.wait(max(0,scheduled-time.monotonic())):break
                for f in tuple(pending):
                    if f.done():pending.remove(f);f.result()
                if stop.is_set():break
                if time.monotonic()-scheduled>max_lateness:
                    unsent.append({'sequence':n,'reason':'GENERATOR_LATE'});continue
                if len(pending)>=clients:
                    unsent.append({'sequence':n,'reason':'CLIENT_BUDGET_BUSY'});continue
                pending.add(pool.submit(call,n,scheduled))
        except Exception as exc:error=type(exc).__name__;stop.set()
        finally:
            for f in pending:
                try:f.result()
                except Exception as exc:error=error or type(exc).__name__
    late=sum(r['lateness_seconds']>max_lateness for r in records)
    return {'state':'INTERRUPTED_REQUIRES_INSPECTION' if error else 'INCONCLUSIVE_GENERATOR' if unsent or late else 'COMPLETE',
        'error_class':error,'mode':'independent_arrivals','target_rate':rate,'target_arrivals':target,
        'actual_completed_calls':len(records),'unsent':unsent,'not_scheduled_after_stop':target-len(records)-len(unsent),
        'late_sent_calls':late,
        'max_observed_lateness_seconds':max((r['lateness_seconds'] for r in records),default=0),
        'measurement_seconds':seconds,'records':records}
