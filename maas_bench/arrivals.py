"""Bounded open arrivals and bursts with durable intents; never retries a request."""
from concurrent.futures import ThreadPoolExecutor,wait,FIRST_COMPLETED
import hashlib
import math
import threading
import time

from .contracts import integer


def drive(invoke,*,clients,seconds=None,rate=None,count=None,stop=None,max_calls=100000,max_lateness=.1):
    integer(clients,'clients');integer(max_calls,'max_calls',maximum=100000)
    burst=count is not None
    if burst:
        integer(count,'count',maximum=max_calls)
        if rate is not None or seconds is not None:raise ValueError('Burst or arrival clock, not both')
        target=count
    else:
        if not all(type(v) in (int,float) and math.isfinite(v) and v>0 for v in (seconds,rate)):
            raise ValueError('Finite positive clock required')
        target=math.ceil(seconds*rate)
        if target>max_calls:raise ValueError('Bounded arrival budget exceeded')
    stop=stop or threading.Event();records=[];unsent=[];lock=threading.Lock();pending=set();started=time.monotonic();error=None
    def call(n,scheduled):
        if stop.is_set():return
        a=time.monotonic()
        try:
            value=invoke(n)
            with lock:records.append({'sequence':n,'sent_offset':a-started,'lateness_seconds':a-scheduled,
                'completed_offset':time.monotonic()-started,'outcome':value})
        except BaseException:
            stop.set();raise
    with ThreadPoolExecutor(max_workers=clients) as pool:
        try:
            for n in range(target):
                if stop.is_set():break
                if burst:
                    while len(pending)>=clients:
                        done,_=wait(pending,timeout=.05,return_when=FIRST_COMPLETED)
                        for f in done:pending.remove(f);f.result()
                        if stop.is_set():break
                    scheduled=time.monotonic()
                else:
                    scheduled=started+n/rate
                    if stop.wait(max(0,scheduled-time.monotonic())):break
                    for f in tuple(pending):
                        if f.done():pending.remove(f);f.result()
                    reason='GENERATOR_LATE' if time.monotonic()-scheduled>max_lateness else 'CLIENT_BUDGET_BUSY' if len(pending)>=clients else None
                    if reason:unsent.append({'sequence':n,'reason':reason});continue
                if stop.is_set():break
                pending.add(pool.submit(call,n,scheduled))
        except BaseException as exc:error=type(exc).__name__;stop.set()
        finally:
            for f in pending:
                try:f.result()
                except BaseException as exc:error=error or type(exc).__name__
            if not burst and not stop.is_set():stop.wait(max(0,started+seconds-time.monotonic()))
    late=0 if burst else sum(r['lateness_seconds']>max_lateness for r in records)
    return {'state':'INTERRUPTED_REQUIRES_INSPECTION' if error or stop.is_set() else 'INCONCLUSIVE_GENERATOR' if unsent or late else 'COMPLETE',
        'mode':'bounded_burst' if burst else 'independent_arrivals','target_arrivals':target,'target_rate':rate,
        'records':records,'unsent':unsent,'late_sent_calls':late,'actual_completed_calls':len(records),
        'measurement_seconds':time.monotonic()-started,'error_class':error,
        'not_scheduled_after_stop':target-len(records)-len(unsent)}


class AdmissionBackend:
    """A strict task client whose expected QUEUE_FULL is a confirmed rejection."""
    def __init__(self,backend,emit=lambda _:None):
        self.backend=backend;self.emit=emit;self.lock=threading.Lock();self.accepted=[];self.rejected=[]

    def submit(self,sequence):
        b=self.backend;project,prefix,model,revision=b.observer.scope;key=prefix+str(sequence)
        body={'model':model,'input':b.fixtures[sequence%len(b.fixtures)]['input'],
            'parameters':b.fixtures[sequence%len(b.fixtures)].get('parameters',{}),'timeout_seconds':b.timeout,'max_attempts':1}
        request_id='e3-'+hashlib.sha256(key.encode()).hexdigest()[:32]
        self.emit({'kind':'intent','sequence':sequence,'idempotency_key':key,'request_id':request_id,'at_epoch':time.time()})
        a=time.monotonic();response=b.client.post('/maas/v1/tasks',headers={'Idempotency-Key':key,'X-Request-ID':request_id},json=body)
        data=response.json();record={'sequence':sequence,'http_status':response.status_code,'seconds':time.monotonic()-a,
            'request_id':response.headers.get('x-request-id'),'idempotency_key':key,'finished_epoch':time.time()}
        if response.status_code==202:
            if data.get('model')!=model or data.get('revision')!=revision or not data.get('id'):raise RuntimeError('Unexpected task acknowledgement')
            record['task_id']=data['id']
            with self.lock:
                if data['id'] in self.accepted:raise RuntimeError('Duplicate task acknowledgement')
                self.accepted.append(data['id'])
        elif response.status_code==429 and data.get('error',{}).get('code')=='QUEUE_FULL':
            record['error_code']='QUEUE_FULL'
            with self.lock:self.rejected.append(key)
        else:raise RuntimeError('Unexpected task admission response; preserve intent, never replay')
        self.emit({'kind':'response',**record});return record

    def rejected_absent(self):
        o=self.backend.observer
        with o.lock:
            count=o.connection.execute('SELECT count(*)::int n FROM tasks WHERE project=%s AND idempotency_key=ANY(%s)',(o.scope[0],self.rejected)).fetchone()['n']
        if count:raise RuntimeError('Rejected fresh requests created tasks')
        return len(self.rejected)
