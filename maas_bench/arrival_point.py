"""Open-arrival real OCR point, distinct from continuously supplied capacity."""
import json
import os
from pathlib import Path
import threading
import time
import httpx

from . import matrix
from .arrivals import AdmissionBackend,drive
from .backend import DatabaseObserver,TaskBackend
from .contracts import Parameters
from .resources import ResourceGuard
from .runtime import ControllerProbe
from .saturation import SupplyMonitor
from .snapshot import RuntimeFence,digest,read_private,read_secret,save_private


def quantile(values,q):
    return sorted(values)[min(len(values)-1,int((len(values)-1)*q))] if values else None


def execute_arrivals(config):
    manifest=read_private(config['fixture_file']);matrix.validate_execution(config,digest(manifest))
    mode=config['load_mode'];policy=config['arrival_policy']
    if mode not in ('independent_arrivals','bounded_burst'):raise ValueError('Arrival mode required')
    p=Parameters(**config['parameters']);original=read_private(config['snapshot_file']);fence=RuntimeFence(original)
    if original['parameters']!=p.to_dict():raise ValueError('Parameters not applied')
    if mode=='independent_arrivals' and not 10<=policy['seconds']<=300:raise ValueError('Bounded live arrival window required')
    if mode=='bounded_burst' and not 1<=policy['count']<=200:raise ValueError('Bounded live burst required')
    folder=Path(config['output_parent'])/config['run_id'];folder.mkdir(mode=0o700,exist_ok=False)
    probe=ControllerProbe(config['source_root'],config['model_id'],config['profile_id'],digest(manifest));fence.check(probe.capture())
    observer=DatabaseObserver(read_secret(config['database_dsn_file']),config['project'],config['logical_model'],config['revision'],'e3-'+config['run_id']+':')
    log=os.fdopen(os.open(folder/'events.jsonl',os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600),'w');lock=threading.Lock()
    stop=threading.Event();halt=threading.Event();errors=[];samples=[]
    def emit(row):
        with lock:
            log.write(json.dumps(row)+'\n');log.flush()
            if row['kind'] not in ('sample','resources'):os.fsync(log.fileno())
    def fail(exc):
        errors.append(type(exc).__name__);emit({'kind':'failure','private_message':str(exc)});stop.set()
    monitor=SupplyMonitor(p,original['generation'],.25);resources=ResourceGuard(original['workers']);threads=[]
    try:
        initial=observer.snapshot()
        if any(initial[k] for k in ('global_queued','running','sync_running','unknown','sync_unknown')):raise RuntimeError('Exclusive idle arrival window required')
        from .fixtures import prepared_fixtures
        fixtures,workload=prepared_fixtures(config,manifest)
        with httpx.Client(base_url=config['base_url'],headers={'Authorization':'Bearer '+read_secret(config['customer_key_file'])},timeout=10,
            trust_env=False,transport=httpx.HTTPTransport(retries=0,verify=config.get('ca_file',True))) as client:
            backend=TaskBackend(client,observer,fixtures);backend.prepare();admission=AdmissionBackend(backend,emit)
            def sample():
                previous=time.monotonic()
                try:
                    while not halt.is_set():
                        a=time.monotonic();row=backend.snapshot();b=time.monotonic();monitor.observe(row,a,b,False)
                        if b-previous>.25:raise RuntimeError('Arrival activity sample gap')
                        previous=b;samples.append(row);emit({'kind':'sample',**row});halt.wait(.05)
                except Exception as exc:fail(exc)
            def watch():
                try:
                    while not halt.is_set():
                        fence.check(probe.capture());rows=probe.resources(original);host=resources.controller();resources.check(rows,host,time.monotonic())
                        emit({'kind':'resources','workers':rows,'controller':host});halt.wait(.5)
                except Exception as exc:fail(exc)
            threads=[threading.Thread(target=f) for f in (sample,watch)]
            for t in threads:t.start()
            result=drive(admission.submit,clients=policy.get('clients',16),stop=stop,
                **({'count':policy['count']} if mode=='bounded_burst' else {'seconds':policy['seconds'],'rate':policy['rate']}))
            if result['state']!='COMPLETE' or errors:raise RuntimeError('Arrival clock incomplete; no replay')
            submitted_end=time.monotonic();deadline=submitted_end+150
            while True:
                rows=backend.reconcile(admission.accepted)
                if errors:raise RuntimeError('Guard stopped arrival drain')
                if any(r['state'] in ('FAILED','CANCELLED') for r in rows):raise RuntimeError('Accepted arrival failed')
                if len(rows)==len(admission.accepted) and all(r['state']=='SUCCEEDED' for r in rows):break
                if time.monotonic()>deadline:raise RuntimeError('Arrival drain deadline')
                time.sleep(.05)
            drain=time.monotonic()-submitted_end
            if any(r['attempts']!=1 or r['events']!=1 for r in rows):raise RuntimeError('Arrival attempt/usage mismatch')
            rejected=admission.rejected_absent();downloads=backend.verify_results(rows)
            halt.set()
            for t in threads:t.join(30)
            if errors or any(t.is_alive() for t in threads):raise RuntimeError('Arrival observer incomplete')
            fence.check(probe.capture())
            with observer.lock:
                timings=observer.connection.execute('''SELECT extract(epoch FROM a.started_at-t.created_at)::float8 waiting,
                  extract(epoch FROM a.ended_at-a.started_at)::float8 executing,
                  extract(epoch FROM t.updated_at-t.created_at)::float8 total FROM tasks t
                  JOIN task_attempts a ON a.task_id=t.id AND a.attempt=t.attempts
                  WHERE t.project=%s AND starts_with(t.idempotency_key,%s) AND t.state='SUCCEEDED' ''',observer.scope[:2]).fetchall()
            http=[r['outcome'] for r in result['records']]
            summary={k:v for k,v in result.items() if k!='records'}
            summary.update(case_id=config['case_id'],point_id=config['point_id'],parameters=p.to_dict(),
                **workload,
                accepted=len(rows),queue_full_rejections=rejected,drain_seconds=drain,download_verification=downloads,
                queue_peak=max((s['project_queued'] for s in samples),default=0),
                first_queue=samples[0]['project_queued'] if samples else None,last_queue=samples[-1]['project_queued'] if samples else None,
                sample_count=len(samples),accepted_task_ids=admission.accepted,
                wait_p95=quantile([r['waiting'] for r in timings],.95),wait_p99=quantile([r['waiting'] for r in timings],.99),
                execution_p95=quantile([r['executing'] for r in timings],.95),total_p95=quantile([r['total'] for r in timings],.95),
                total_p99=quantile([r['total'] for r in timings],.99),
                response_latency={str(code):{'count':sum(r['http_status']==code for r in http),
                    'p95':quantile([r['seconds'] for r in http if r['http_status']==code],.95),
                    'p99':quantile([r['seconds'] for r in http if r['http_status']==code],.99)} for code in (202,429)},
                natural_empty_queue_allowed=True,maximum_sustained_capacity_claim=False)
            save_private(folder/'summary.json',summary);return summary
    except BaseException as exc:
        stop.set();save_private(folder/'interrupted.json',{'state':'INTERRUPTED_REQUIRES_INSPECTION','error_class':type(exc).__name__});raise
    finally:
        halt.set()
        for t in threads:t.join(30)
        observer.close();log.close()
