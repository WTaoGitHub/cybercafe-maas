"""Disposable real HTTP/API/PG experiment with real auth and mocked OCR only.

Run inside the separately resource-limited test container. The caller provides
an isolated TLS proxy and an explicitly disposable *_test database. This module
never operates a production database schema, worker, VM, or Docker daemon.
"""
import hashlib
import io
import json
import os
from pathlib import Path
import threading
import time
import httpx
import psycopg
import uvicorn
from PIL import Image

from .arrivals import AdmissionBackend,drive
from .arrival_point import quantile
from .backend import DatabaseObserver,TaskBackend
from .prepared import timed_prepared
from .resources import ResourceGuard
from .snapshot import read_private,read_secret,save_private,replace_private


def window_summary(value):
    records=[r['outcome'] for r in value['records'] if r['completed_offset']<=value['measurement_seconds']]
    return {**{k:v for k,v in value.items() if k!='records'},'http_groups':{
        str(code):{'count':sum(r['http_status']==code for r in records),
            'p95':quantile([r['seconds'] for r in records if r['http_status']==code],.95),
            'p99':quantile([r['seconds'] for r in records if r['http_status']==code],.99)} for code in (202,429)}}


class MockConsumers:
    def __init__(self,service,count):
        from maas_platform.worker import Worker
        self.halt=threading.Event();self.paused=False;self.active=0;self.lock=threading.Lock();self.errors=[];self.threads=[]
        def loop():
            worker=Worker(service,stop_claiming=self.halt)
            try:
                while not self.halt.is_set():
                    with self.lock:
                        allowed=not self.paused
                        if allowed:self.active+=1
                    if not allowed:self.halt.wait(.005);continue
                    try:worked=worker.run_once()
                    finally:
                        with self.lock:self.active-=1
                    if not worked:self.halt.wait(.005)
            except BaseException as exc:self.errors.append(type(exc).__name__);self.halt.set()
        for _ in range(count):
            t=threading.Thread(target=loop);t.start();self.threads.append(t)

    def pause(self):
        with self.lock:self.paused=True
        until=time.monotonic()+35
        while True:
            with self.lock:active=self.active
            if not active:return
            if time.monotonic()>until or self.errors:raise RuntimeError('Disposable consumers failed to drain')
            time.sleep(.01)

    def resume(self):
        with self.lock:self.paused=False

    def close(self):
        self.halt.set()
        for t in self.threads:t.join(35)
        if any(t.is_alive() for t in self.threads):raise RuntimeError('Disposable consumer did not stop')


def run(config,test_auth_client=None):
    from maas_platform.api import create_app
    from maas_platform.config import Settings
    from maas_platform.ocr_connector import OCRAdapter
    from maas_platform.auth import AuthConnector
    validation=config.get('tool_validation_only',False)
    if test_auth_client is not None and not validation:raise ValueError('Authentication stub cannot be used for capacity evidence')
    out=Path(config['output_directory']);out.mkdir(mode=0o700,exist_ok=False)
    dsn=read_secret(config['test_database_dsn_file'])
    if not psycopg.conninfo.conninfo_to_dict(dsn)['dbname'].endswith('_test'):raise ValueError('Explicitly disposable test database required')
    if config.get('allow_reset_test_schema') is not True:raise ValueError('Explicit test schema reset required')
    with psycopg.connect(dsn) as c:c.execute('DROP SCHEMA public CASCADE');c.execute('CREATE SCHEMA public')
    settings=Settings(dsn,out/'artifacts',config['gpustack_url'],Path(config['bindings_file']),Path(config['catalog_file']),Path(config['pepper_file']),
        max_running=12,max_project_running=12,max_queued=100,max_project_queued=100,min_free_bytes=2*1024**3)
    def mock_execute(self,ctx):
        ctx.read_input();ctx.check()
        return {'text':'hello','lines':[{'text':'hello','score':.99,'box':[[0,0],[2,0],[2,2],[0,2]]}],'elapsed_ms':0,'node':'CONTROLLED-MOCK-NO-GPU'}
    OCRAdapter.execute=mock_execute
    app=create_app(settings,AuthConnector(settings,test_auth_client));server=uvicorn.Server(uvicorn.Config(app,host='127.0.0.1',port=config['api_port'],log_level='error',access_log=False))
    server_thread=threading.Thread(target=server.run);server_thread.start();consumers=None;stop=threading.Event();halt=threading.Event();errors=[];resources=[];lock=threading.Lock()
    log=os.fdopen(os.open(out/'events.jsonl',os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600),'w')
    def emit(row):
        with lock:
            log.write(json.dumps(row)+'\n');log.flush()
            if row['kind'] not in ('sample','resources'):os.fsync(log.fileno())
    result={'state':'RUNNING','profile_id':'tool-validation-auth-stub-no-capacity' if validation else 'real-api-pg-tls-auth-mocked-ocr-cpu4-r1','production_capacity_claim':False,
        'authentication':'validation stub only' if validation else 'unchanged real GPUStack binding and model authorization; no stub/cache','execution':'16 controlled mock consumers, G/P=12, original claim/finish/auth logic',
        'isolated_limits':{'G':12,'P':12,'Qg':100,'Qp':100},'acceptance':[],'rates':[],'rejection':[],'recovery':[]}
    def checkpoint():replace_private(out/'status.json',result)
    def resource_watch():
        guard=ResourceGuard([])
        try:
            while not halt.is_set():
                row=guard.controller();guard.check([],row,time.monotonic())
                cg=Path('/sys/fs/cgroup');row['cgroup']={}
                for name in ('cpu.stat','cpu.max','memory.current','memory.max'):
                    path=cg/name
                    if path.is_file():row['cgroup'][name]=path.read_text().strip()
                resources.append(row);emit({'kind':'resources',**row});halt.wait(.25)
        except BaseException as exc:errors.append(type(exc).__name__);stop.set()
    watcher=threading.Thread(target=resource_watch)
    try:
        deadline=time.monotonic()+20
        while not server.started:
            if not server_thread.is_alive() or time.monotonic()>deadline:raise RuntimeError('Disposable HTTP API did not start')
            time.sleep(.05)
        consumers=MockConsumers(app.state.service,16);watcher.start()
        with httpx.Client(base_url=config['base_url'],headers={'Authorization':'Bearer '+read_secret(config['customer_key_file'])},timeout=10,trust_env=False,
            transport=httpx.HTTPTransport(retries=0,verify=config['ca_file'])) as client:
            im=Image.new('RGB',(4,3),'white');buf=io.BytesIO();im.save(buf,format='PNG');raw=buf.getvalue()
            uploaded=client.post('/maas/v1/artifacts',content=raw,headers={'Content-Type':'image/png'})
            if uploaded.status_code!=201:raise RuntimeError('Disposable input upload failed')
            fixtures=[{'sha256':hashlib.sha256(raw).hexdigest(),'input':{'image':{'artifact_id':uploaded.json()['id']}},'expected_text_sha256':hashlib.sha256(b'hello').hexdigest()}]
            number=0
            def point(c,kind,seconds=60,rate=None,clients_count=None):
                nonlocal number
                prefix='e3-http-isolated-20261029-'+str(number)+':';number+=1
                obs=DatabaseObserver(dsn,config['project'],'ocr.default',config['revision'],prefix,statement_ms=1000)
                b=TaskBackend(client,obs,fixtures);b.prepare();a=AdmissionBackend(b,emit);samples=[];sample_halt=threading.Event();probes=[]
                def sample():
                    try:
                        last_probe=0
                        while not sample_halt.is_set():
                            row=obs.snapshot()
                            if row['unknown'] or row['sync_unknown'] or row['failed'] or row['project_queued']>100 or row['running']>12:raise RuntimeError('Disposable activity invariant')
                            with obs.lock:
                                activity=obs.connection.execute("SELECT count(*)::int connections,count(*) FILTER(WHERE wait_event_type='Lock')::int lock_waiters FROM pg_stat_activity WHERE datname=current_database()").fetchone()
                            row.update(activity);samples.append(row);emit({'kind':'sample',**row})
                            target=a.accepted[-1] if a.accepted else pre[-1] if kind=='reject' and pre else None
                            if target and time.monotonic()-last_probe>=1:
                                began=time.monotonic();response=client.get('/maas/v1/tasks/'+target)
                                if response.status_code!=200:raise RuntimeError('Task query probe failed during HTTP pressure')
                                probes.append(time.monotonic()-began);last_probe=time.monotonic()
                            sample_halt.wait(.1)
                    except BaseException as exc:errors.append(type(exc).__name__);stop.set()
                sampler=threading.Thread(target=sample);sampler.start()
                try:
                    if rate is not None:value=drive(a.submit,clients=c,seconds=seconds,rate=rate,stop=stop)
                    elif clients_count is not None:value=drive(a.submit,clients=c,count=clients_count,stop=stop)
                    else:value=timed_prepared(lambda _,n:a.submit(n),[None],c,seconds=seconds,min_successes=1 if kind=='reject' or validation else 200,max_seconds=seconds if kind=='reject' else max(seconds,180),stop=stop,max_calls=10000 if kind=='reject' else 100000)
                    if value['state']!='COMPLETE' or errors or consumers.errors:
                        save_private(out/('point-'+str(number)+'-interrupted.json'),window_summary(value))
                        raise RuntimeError('HTTP experiment incomplete: '+value['state'])
                    summary=window_summary(value);summary.update(kind=kind,clients=c,queue_peak=max((r['own_queued'] for r in samples),default=0),
                        db_peak_connections=max((r['connections'] for r in samples),default=0),db_peak_lock_waiters=max((r['lock_waiters'] for r in samples),default=0),
                        query_probe_count=len(probes),query_probe_p95=quantile(probes,.95))
                    if kind!='reject':
                        began=time.monotonic()
                        while True:
                            rows=b.reconcile(a.accepted)
                            if errors or consumers.errors:raise RuntimeError('Disposable drain guard failed')
                            if len(rows)==len(a.accepted) and all(r['state']=='SUCCEEDED' for r in rows):break
                            if time.monotonic()-began>150 or any(r['state'] in ('FAILED','CANCELLED') for r in rows):raise RuntimeError('Disposable deadline/drain failed')
                            time.sleep(.02)
                        if any(r['attempts']!=1 or r['events']!=1 for r in rows):raise RuntimeError('Disposable duplicate usage/attempt')
                        b.verify_results(rows);summary['drain_seconds']=time.monotonic()-began
                    elif a.accepted:raise RuntimeError('Full-queue isolated reject path unexpectedly accepted')
                    summary['rejected_without_task']=a.rejected_absent()
                    summary['stable_202_window']=bool(kind=='accept' and not a.rejected and samples and max(r['own_queued'] for r in samples)<100 and sum(r['own_queued'] for r in samples[-max(1,len(samples)//4):])/max(1,len(samples)//4)<=sum(r['own_queued'] for r in samples[:max(1,len(samples)//4)])/max(1,len(samples)//4)+12)
                    save_private(out/('point-'+str(number)+'.json'),summary);return summary
                finally:
                    sample_halt.set();sampler.join(15);obs.close()
            tiers=(1,2) if validation else (1,2,4,8,16)
            for c in tiers:result['acceptance'].append(point(c,'accept',seconds=1 if validation else 60));checkpoint()
            healthy=[r for r in result['acceptance'] if r['stable_202_window'] and r['http_groups']['202']['p95']<=.5 and r['http_groups']['202']['p99']<=1.5]
            if healthy:
                top=max(healthy,key=lambda r:r['clients']);c=top['clients'];mu=top['http_groups']['202']['count']/top['measurement_seconds']
                for _ in range(0 if validation else 2):result['acceptance'].append(point(c,'accept'));checkpoint()
            else:
                # This is a measured consumer/DB envelope, not permission to
                # raise Q or change the fixed consumer profile mid-experiment.
                c=1;mu=min(r['http_groups']['202']['count']/r['measurement_seconds'] for r in result['acceptance'])
                result['closed_loop_limit']='NO_STABLE_TIER; controlled consumer/DB constraint remains part of this profile'
            # The independent clock needs an explicitly separate request budget;
            # do not inherit the fastest closed-loop tier's number of lanes.
            for fraction in ((.5,) if validation else (.5,.8,1,1.2)):result['rates'].append(point(16,'rate',seconds=1 if validation else 60,rate=mu*fraction));checkpoint()
            consumers.pause()
            pre=[]
            for n in range(100):
                emit({'kind':'intent','idempotency_key':'e3-http-prefill-20261029:'+str(n),'at_epoch':time.time()})
                r=client.post('/maas/v1/tasks',headers={'Idempotency-Key':'e3-http-prefill-20261029:'+str(n)},json={'model':'ocr.default','input':fixtures[0]['input'],'timeout_seconds':120,'max_attempts':1})
                if r.status_code!=202:raise RuntimeError('Isolated full-queue prefill failed')
                pre.append(r.json()['id']);emit({'kind':'prefill_accepted','task_id':r.json()['id'],'request_id':r.headers.get('x-request-id')})
            for c in tiers:result['rejection'].append(point(c,'reject',seconds=1 if validation else 10));checkpoint()
            consumers.resume();began=time.monotonic()
            # Explicit ordinary observer; no cancellation/deletion/reset on recovery.
            obs=DatabaseObserver(dsn,config['project'],'ocr.default',config['revision'],'e3-http-prefill-20261029:',statement_ms=1000)
            try:
                b=TaskBackend(client,obs,fixtures)
                while True:
                    rows=b.reconcile(pre)
                    if errors or consumers.errors:raise RuntimeError('Recovery observer failed')
                    if len(rows)==len(pre) and all(r['state']=='SUCCEEDED' for r in rows):break
                    if time.monotonic()-began>150 or any(r['state'] in ('FAILED','CANCELLED') for r in rows):raise RuntimeError('Recovery exceeded original task deadline')
                    time.sleep(.05)
                if any(r['attempts']!=1 or r['events']!=1 for r in rows):raise RuntimeError('Recovery usage mismatch')
                b.verify_results(rows)
            finally:obs.close()
            result['full_queue_drain_seconds']=time.monotonic()-began
            result['recovery'].append(point(1,'recovery',clients_count=3));checkpoint()
        result.update(state='COMPLETE',resource_samples=len(resources),controller_cpu_peak=max((r['cpu_percent'] or 0 for r in resources),default=0),
            cgroup_before=resources[0].get('cgroup') if resources else None,cgroup_after=resources[-1].get('cgroup') if resources else None);checkpoint()
    except BaseException as exc:result.update(state='INTERRUPTED_REQUIRES_INSPECTION',error_class=type(exc).__name__);checkpoint();raise
    finally:
        halt.set()
        if watcher.ident is not None:watcher.join(15)
        if consumers:consumers.close()
        server.should_exit=True;server_thread.join(20);log.close()


if __name__=='__main__':
    import argparse
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--config',required=True);p.add_argument('--execute',action='store_true');a=p.parse_args()
    if a.execute:run(read_private(a.config))
    else:print(json.dumps({'state':'DRY_RUN','execution':False}))
