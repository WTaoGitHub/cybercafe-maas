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
from .resources import ResourceGuard,observe_resources
from .runtime import ControllerProbe
from .saturation import SupplyMonitor
from .snapshot import RuntimeFence,digest,read_private,read_secret,save_private


def quantile(values,q):
    return sorted(values)[min(len(values)-1,int((len(values)-1)*q))] if values else None


def execute_arrivals(config):
    manifest=read_private(config['fixture_file']);candidate_policy=None
    if config.get('candidate_t6_protocol'):
        from .candidate_t6 import validate_execution
        candidate_policy=validate_execution(config,manifest)
    elif config.get('candidate_queue_protocol'):
        from .candidate_queue import validate_execution
        candidate_policy=validate_execution(config,manifest)
    else:matrix.validate_execution(config,digest(manifest))
    mode=config['load_mode'];policy=config['arrival_policy']
    if mode not in ('independent_arrivals','bounded_burst'):raise ValueError('Arrival mode required')
    p=Parameters(**config['parameters']);original=read_private(config['snapshot_file']);fence=RuntimeFence(original)
    if original['parameters']!=p.to_dict():raise ValueError('Parameters not applied')
    if mode=='independent_arrivals' and not 10<=policy['seconds']<=300:raise ValueError('Bounded live arrival window required')
    if mode=='bounded_burst' and not 1<=policy['count']<=200:raise ValueError('Bounded live burst required')
    qos=candidate_policy
    if qos:
        from .queue_policy import CompletionClock
        clock=CompletionClock()
    elif config.get('qos_policy_file') and config['case_id']=='T4-QUEUE':
        from .queue_policy import load_policy,CompletionClock
        qos=load_policy(config);clock=CompletionClock()
    folder=Path(config['output_parent'])/config['run_id'];folder.mkdir(mode=0o700,exist_ok=False)
    probe=ControllerProbe(config['source_root'],config['model_id'],config['profile_id'],digest(manifest));fence.check(probe.capture())
    from .experiment import check_runtime,stamp
    check_runtime(config,original)
    observer=DatabaseObserver(read_secret(config['database_dsn_file']),config['project'],config['logical_model'],config['revision'],'e3-'+config['run_id']+':')
    log=os.fdopen(os.open(folder/'events.jsonl',os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600),'w');lock=threading.Lock()
    stop=threading.Event();halt=threading.Event();errors=[];samples=[];query_probes=[]
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
                previous=time.monotonic();last_completion_sample=0
                try:
                    while not halt.is_set():
                        a=time.monotonic();include=bool(qos) and a-last_completion_sample>=.5
                        row=backend.snapshot(include_ids=include);b=time.monotonic();monitor.observe(row,a,b,False)
                        if include:
                            new=clock.observe(row);row['new_success_ids']=new;last_completion_sample=b
                        row.pop('success_ids',None)
                        if b-previous>.25:raise RuntimeError('Arrival activity sample gap')
                        previous=b;samples.append(row);emit({'kind':'sample',**row});halt.wait(.05)
                except Exception as exc:fail(exc)
            def watch():
                try:
                    while not halt.is_set():
                        fence.check(probe.capture());observe_resources(resources,probe,original,emit);halt.wait(.5)
                except Exception as exc:fail(exc)
            def query():
                try:
                    while not halt.is_set():
                        with admission.lock:ident=admission.accepted[-1] if admission.accepted else None
                        if ident:
                            began=time.monotonic();response=client.get('/maas/v1/tasks/'+ident)
                            if response.status_code!=200:raise RuntimeError('Task query unavailable during candidate pressure')
                            query_probes.append(time.monotonic()-began);emit({'kind':'http_query_probe','status':200,'seconds':query_probes[-1]})
                        halt.wait(1 if ident else .05)
                except Exception as exc:fail(exc)
            threads=[threading.Thread(target=f) for f in ((sample,watch,query) if candidate_policy else (sample,watch))]
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
            if qos:clock.observe(backend.snapshot(include_ids=True))
            timings=observer.task_timings()
            if len(timings)!=len(admission.accepted):raise RuntimeError('Accepted timing rows missing')
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
            if qos:
                visible,observations=clock.verify(timings)
                spans=[r['sent_offset'] for r in result['records']]
                emit({'kind':'completion_observation_final','observations':observations})
                log.flush();os.fsync(log.fileno())
                summary.update(qos_policy_id=qos['policy_id'],qos_policy_sha256=config['qos_policy_sha256'],
                    qos_class=config['qos_class'],qos_repeat=config.get('qos_repeat'),correctness_verified=True,
                    wait_max=max((r['waiting'] for r in timings),default=None),
                    observed_completion_p95=quantile(visible,.95),observed_completion_max=max(visible,default=None),
                    completion_observation_sample_seconds=.5,task_deadline_seconds=120,
                    submission_span_seconds=max(spans)-min(spans) if spans else 0,
                    total_clock_kind='DB_UPDATED_AT_NOT_DURABLE_E2E',
                    completion_clock_kind='FIRST_COMMITTED_SUCCESS_OBSERVATION_UPPER_BOUND_NOT_CLIENT_LATENCY')
            if candidate_policy:
                summary.update(query_probe_count=len(query_probes),query_probe_p95=quantile(query_probes,.95),query_probe_max=max(query_probes,default=None))
                if config.get('candidate_t6_protocol'):summary.update(candidate_t6_protocol=config['candidate_t6_protocol'],selection_sha256=config['selection_file_sha256'],queue_proof_sha256=config['queue_proof_file_sha256'])
                else:summary.update(candidate_queue_protocol=config['candidate_queue_protocol'],candidate_mu_sha256=config['candidate_mu_sha256'],candidate_selection_sha256=config['selection_sha256'])
            summary.update(**stamp(config));check_runtime(config,probe.capture())
            save_private(folder/'summary.json',summary);return summary
    except BaseException as exc:
        stop.set();save_private(folder/'interrupted.json',{'state':'INTERRUPTED_REQUIRES_INSPECTION','error_class':type(exc).__name__});raise
    finally:
        halt.set()
        for t in threads:t.join(30)
        observer.close();log.close()
