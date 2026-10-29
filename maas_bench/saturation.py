"""Continuously replenish ready work without waiting for result downloads.

The backend supplies committed database snapshots. Queue samples are evidence at
their measured resolution, never a claim of mathematically continuous occupancy.
"""
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
import math
import threading
import time
from .contracts import Parameters, integer


class InvalidSupply(RuntimeError):
    def __init__(self, status):
        super().__init__(status)
        self.status = status


@dataclass(frozen=True)
class RunPolicy:
    seconds: float = 60
    min_successes: int = 200
    max_seconds: float = 300
    sample_seconds: float = .05
    max_sample_gap: float = .25
    prefill_seconds: float = 30
    drain_seconds: float = 150
    submit_clients: int = 4
    max_submissions: int = 200000

    def __post_init__(self):
        for name in ('seconds','max_seconds','sample_seconds','max_sample_gap','prefill_seconds','drain_seconds'):
            value=getattr(self,name)
            if isinstance(value,bool) or not isinstance(value,(int,float)) or not math.isfinite(value) or value<=0:
                raise ValueError('Positive finite time budgets required')
        if self.max_seconds<self.seconds or self.sample_seconds>.1 or self.max_sample_gap<self.sample_seconds:
            raise ValueError('Inconsistent measurement/sampling budgets')
        integer(self.submit_clients,'submit_clients')
        integer(self.min_successes,'min_successes',maximum=200000)
        integer(self.max_submissions,'max_submissions',maximum=200000)
        if self.min_successes>self.max_submissions:raise ValueError('Completion target exceeds submission budget')


def watermarks(parameters, low=None, high=None):
    # Reserve at least one queue position; the pending submissions also consume
    # the high-water budget in the dispatcher rather than being ignored.
    high=min(4*parameters.async_slots,parameters.effective_queue-1) if high is None else high
    low=min(2*parameters.async_slots,high-1) if low is None else low
    if type(low) is not int or type(high) is not int or not 1<=low<high<parameters.effective_queue:
        raise ValueError('Watermarks require 1 <= low < high < effective queue')
    return low,high


class SupplyMonitor:
    def __init__(self,parameters,generation,max_gap):
        self.parameters=parameters;self.generation=generation;self.max_gap=max_gap
        self.last=None;self.maximum_gap=0.;self.minimum_ready=None;self.empty_events=[];self.samples=0

    def observe(self,row,started_at,finished_at,measuring):
        for key in ('ready','global_queued','project_queued','running','sync_running','project_running','project_sync_running','unknown','sync_unknown','failed','successes','generation'):
            if type(row[key]) is not int or row[key]<0:raise ValueError('Invalid required activity counter')
        p=self.parameters
        if row['unknown'] or row['sync_unknown']:raise RuntimeError('UNKNOWN; stop dispatch')
        if row['paused'] or row['generation']!=self.generation:raise RuntimeError('Maintenance ownership changed')
        if row['running']+row['sync_running']>p.global_limit or row['project_running']+row['project_sync_running']>p.project_limit:
            raise RuntimeError('Execution quota exceeded')
        if row['global_queued']>p.global_queue or row['project_queued']>p.project_queue:
            raise RuntimeError('Queue quota exceeded')
        if row['failed']:raise RuntimeError('Accepted task failed or was cancelled')
        if measuring:
            gap=finished_at-self.last if self.last is not None else finished_at-started_at
            self.maximum_gap=max(self.maximum_gap,gap,finished_at-started_at)
            if gap>self.max_gap or finished_at-started_at>self.max_gap:
                raise InvalidSupply('INCONCLUSIVE_SUPPLY_EVIDENCE')
            self.last=finished_at;self.samples+=1
            self.minimum_ready=row['ready'] if self.minimum_ready is None else min(self.minimum_ready,row['ready'])
            if not row['ready']:
                self.empty_events.append({'at_monotonic':finished_at})
                raise InvalidSupply('INVALID_UNDERSUPPLIED')

    def evidence(self):
        return {'sample_count':self.samples,'ready_queue_min':self.minimum_ready,
                'empty_events':self.empty_events,'max_observed_sample_gap_seconds':self.maximum_gap,
                'continuous_occupancy_proven':False,'evidence_kind':'committed_database_samples'}


class SaturationRunner:
    """Backend: snapshot(include_ids=False), submit(sequence), reconcile(task_ids).

    Only this class creates work. Snapshot observation has its own thread;
    downloads/reconciliation happen after new submissions have stopped.
    """
    def __init__(self,backend,parameters,policy,generation,emit=lambda _:None,low=None,high=None):
        self.backend=backend;self.parameters=parameters;self.policy=policy
        self.low,self.high=watermarks(parameters,low,high)
        self.monitor=SupplyMonitor(parameters,generation,policy.max_sample_gap)
        self.emit=emit;self.stop=threading.Event();self.sample_stop=threading.Event()
        self.lock=threading.RLock();self.latest=None;self.failure=None
        self.measuring=False;self.accepted=[];self.records=[];self.samples=[]
        self.accepted_set=set()

    def _event(self,value):
        with self.lock:self.emit(value)

    def _fail(self,exc):
        with self.lock:
            if self.failure is None:self.failure=exc
            self.stop.set()

    def _snapshot(self,include_ids=False):
        with self.lock:accepted_before=len(self.accepted)
        started=time.monotonic();row=self.backend.snapshot(include_ids=include_ids);finished=time.monotonic()
        if not math.isfinite(row['db_at']):raise RuntimeError('Invalid database clock')
        with self.lock:
            self.monitor.observe(row,started,finished,self.measuring)
            self.latest=(row,accepted_before,finished)
            self.samples.append({'at_monotonic':finished,'ready':row['ready'],'running':row['running'],
                                 'sync_running':row['sync_running'],'successes':row['successes'],'measuring':self.measuring,
                                 'snapshot_seconds':finished-started,'db_at':row['db_at'],
                                 'idle_async_slots':max(0,self.parameters.async_slots-row.get('own_running',row['running']))})
            self.emit({'kind':'sample',**self.samples[-1]})
        return row

    def _sample_loop(self):
        while not self.sample_stop.is_set():
            try:self._snapshot()
            except Exception as exc:self._fail(exc);return
            self.sample_stop.wait(self.policy.sample_seconds)

    def _submit(self,sequence):
        if self.stop.is_set():return None
        record={'sequence':sequence,'state':'SENDING','intent_monotonic':time.monotonic()}
        self._event({'kind':'intent',**record})
        try:
            task=self.backend.submit(sequence)
            if not isinstance(task,str) or not task:raise ValueError('Unconfirmed task acceptance')
            with self.lock:
                if task in self.accepted_set:raise RuntimeError('Duplicate task acknowledgement')
                self.accepted_set.add(task)
                self.accepted.append(task)
                record.update(task_id=task,state='ACCEPTED',accepted_monotonic=time.monotonic())
                record['acceptance_seconds']=record['accepted_monotonic']-record['intent_monotonic']
                self.records.append(record);self.emit({'kind':'accepted',**record})
            return record
        except Exception as exc:
            self._fail(exc);self._event({'kind':'submission_failed','sequence':sequence,'error_class':type(exc).__name__})
            raise

    def run(self):
        p=self.policy;summary={'state':'PREFILL','parameters':self.parameters.to_dict(),
            'low_watermark':self.low,'high_watermark':self.high,'policy':p.__dict__.copy()}
        pending=set();sequence=0;refilling=True;start=None;end=None
        sampler=threading.Thread(target=self._sample_loop,name='bench-observer')
        pool=ThreadPoolExecutor(max_workers=p.submit_clients,thread_name_prefix='bench-submit')
        prefill_started=time.monotonic();sampler.start()
        try:
            while True:
                if self.failure:raise self.failure
                done={f for f in pending if f.done()}
                for future in done:future.result()
                pending-=done
                with self.lock:
                    latest=self.latest;accepted_count=len(self.accepted)
                now=time.monotonic()
                if latest:
                    row,accepted_before,snapshot_finished=latest
                    if now-snapshot_finished>p.max_sample_gap:raise InvalidSupply('INCONCLUSIVE_SUPPLY_EVIDENCE')
                    # Accepted since snapshot START may already be visible in it.
                    # Double-reserving is conservative; dropping these credits
                    # would permit accidental overfill during the snapshot race.
                    credits=accepted_count-accepted_before
                    estimated=row['ready']+credits+len(pending)
                    if estimated<=self.low:refilling=True
                    if estimated>=self.high:refilling=False
                    busy=row['running']+(row['sync_running'] if getattr(self.backend,'allow_sync',False) else 0)
                    if start is None and row['ready']>=self.low and busy>=self.parameters.async_slots:
                        self.sample_stop.set();sampler.join(timeout=p.max_sample_gap+1)
                        if sampler.is_alive():raise RuntimeError('Observer failed to stop')
                        with self.lock:
                            start=self._snapshot(include_ids=True)
                            self.measuring=True;self.monitor.last=time.monotonic()
                            self.monitor.observe(start,self.monitor.last,self.monitor.last,True)
                        summary['state']='MEASURING';measure_started=time.monotonic()
                        self.sample_stop.clear();sampler=threading.Thread(target=self._sample_loop,name='bench-observer');sampler.start()
                    if start is not None:
                        elapsed=time.monotonic()-measure_started
                        if elapsed>=p.seconds and row['successes']-start['successes']>=p.min_successes:break
                        if elapsed>=p.max_seconds:raise InvalidSupply('INCONCLUSIVE_INSUFFICIENT_COMPLETIONS')
                    elif now-prefill_started>=p.prefill_seconds:raise InvalidSupply('INCONCLUSIVE_PREFILL')
                    if refilling:
                        count=min(p.submit_clients-len(pending),max(0,self.high-estimated))
                        for _ in range(count):
                            if self.stop.is_set():break
                            if sequence>=p.max_submissions:raise InvalidSupply('INCONCLUSIVE_SUBMISSION_BUDGET')
                            pending.add(pool.submit(self._submit,sequence));sequence+=1
                elif now-prefill_started>=p.prefill_seconds:raise InvalidSupply('INCONCLUSIVE_PREFILL')
                time.sleep(.002)
            # Cut off the window before drain. Do not count tail completions in
            # its numerator. Already-sent acceptance requests still settle.
            self.sample_stop.set();sampler.join(timeout=p.max_sample_gap+1)
            if sampler.is_alive():raise RuntimeError('Observer failed to stop')
            if self.failure:raise self.failure
            end=self._snapshot(include_ids=True)
            duration=end['db_at']-start['db_at']
            if not math.isfinite(duration) or duration<=0:raise RuntimeError('Invalid database measurement clock')
            measured=set(end['success_ids'])-set(start['success_ids'])
            if len(measured)!=end['successes']-start['successes']:raise RuntimeError('Inconsistent boundary counters')
            summary.update(state='DRAINING',measurement_seconds=duration,measured_successes=len(measured),
                           throughput_per_second=len(measured)/duration,measured_task_ids=sorted(measured),
                           window_start_db_epoch=start['db_at'],window_end_db_epoch=end['db_at'])
        except Exception as exc:
            summary.update(state=getattr(exc,'status','INTERRUPTED_REQUIRES_INSPECTION'),error_class=type(exc).__name__)
            self._fail(exc)
        finally:
            self.stop.set();self.sample_stop.set();sampler.join(timeout=p.max_sample_gap+1)
            pool.shutdown(wait=True,cancel_futures=True)
            if hasattr(self.backend,'stop_auxiliary'):
                try:summary['auxiliary']=self.backend.stop_auxiliary()
                except Exception as exc:self._fail(exc)
            if self.failure and summary['state']=='DRAINING':summary.update(state='INTERRUPTED_REQUIRES_INSPECTION',error_class=type(self.failure).__name__)
            summary.update(submitted_intents=sequence,accepted_task_ids=list(self.accepted),supply=self.monitor.evidence())
        # No result download can starve the measurement: this phase is separate.
        if summary['state']=='DRAINING':
            deadline=time.monotonic()+p.drain_seconds
            try:
                while True:
                    rows=self.backend.reconcile(self.accepted)
                    if any(r['state'] in ('FAILED','CANCELLED') for r in rows):raise RuntimeError('Accepted task failed during drain')
                    if len(rows)==len(self.accepted) and all(r['state']=='SUCCEEDED' for r in rows):break
                    if time.monotonic()>=deadline:raise RuntimeError('Drain deadline; inspect accepted IDs')
                    time.sleep(min(.05,p.sample_seconds))
                if any(r['attempts']!=1 or r['events']!=1 for r in rows):raise RuntimeError('Attempt/event mismatch')
                if {r['id'] for r in rows}!=self.accepted_set:raise RuntimeError('Accepted task reconciliation mismatch')
                downloads=self.backend.verify_results(rows) if hasattr(self.backend,'verify_results') else {}
                if self.failure:raise self.failure
                summary.update(state='COMPLETE',reconciliation=rows,
                               tail_completions=len(self.accepted_set-set(end['success_ids'])),result_verification=downloads)
            except Exception as exc:summary.update(state='INTERRUPTED_REQUIRES_INSPECTION',error_class=type(exc).__name__)
        self._event({'kind':'summary',**summary})
        return summary
