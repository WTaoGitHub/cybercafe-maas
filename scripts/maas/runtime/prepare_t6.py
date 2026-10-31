"""Refresh fixed inputs and reserve the whole T6 before changing runtime.

The 50-task size probe is explicitly diagnostic, never a T6 capacity proof.
Uploads/submissions have intent logs and zero transport retries; no resume.
"""
import argparse
import copy
import json
import os
from pathlib import Path
import sys
import time
sys.path.insert(0,str(Path(__file__).resolve().parents[3]))
import httpx
from maas_bench.backend import DatabaseObserver,TaskBackend
from maas_bench.phase import proof_entry
from maas_bench.snapshot import read_private,read_secret,save_private,digest,RuntimeFence
from maas_bench.runtime import ControllerProbe
from maas_bench.experiment import check_runtime,fresh_namespace
from maas_bench.resources import ResourceGuard
from maas_bench.controller import fixed_profile
from maas_bench.fixtures import prepared_fixtures
from maas_bench.t6_preparation import (file_sha,artifact_id,validate_pair,load_refresh,
    require_inputs,live_storage,forecast_bytes,require_budget,install_stop_handlers)


def run(config):
    c=read_private(config['template_file']);claim,_=proof_entry(config['claim_proof_file'],'T5-CLAIM',c)
    mu,_=proof_entry(config['reference_mu_proof_file'],'T3-MU',c)
    rate=max(x['throughput_per_second'] for x in mu['points'])
    root=Path(config['output_directory']);root.mkdir(mode=0o700,exist_ok=False)
    probe=ControllerProbe(c['source_root'],c['model_id'],c['profile_id'],claim['input_sha256'])
    initial=probe.capture();check_runtime(c,initial);RuntimeFence(read_private(config['expected_runtime_file'])).check(initial);fence=RuntimeFence(initial);save_private(root/'before-private.json',initial)
    o=DatabaseObserver(read_secret(c['database_dsn_file']),c['project'],c['logical_model'],c['revision'],'e3-'+config['run_id']+':',statement_ms=1000)
    log=(root/'events-private.jsonl').open('x');os.chmod(root/'events-private.jsonl',0o600)
    def emit(row):
        log.write(json.dumps(row)+'\n');log.flush();os.fsync(log.fileno())
    try:
        state=o.snapshot()
        if any(state[k] for k in ('global_queued','running','sync_running','unknown','sync_unknown','paused')):raise RuntimeError('Preparation requires exclusive idle unpaused runtime')
        fresh_namespace(c,o,config['run_id'],[])
        originals={'ordinary':read_private(c['fixture_file']),'mixed':read_private(claim['mixed_workload_fixture_file'])}
        raw_path=Path(claim['mixed_raw_file']);raw={x['id']:x for x in read_private(raw_path)}
        payloads={}
        for row in originals['mixed']['fixtures']:
            source=raw[row['id']];data=Path(source['file']).read_bytes()
            import hashlib
            if hashlib.sha256(data).hexdigest()!=row['sha256'] or source['sha256']!=row['sha256']:raise ValueError('Frozen raw input changed')
            payloads[row['id']]=(data,source['media_type'])
        if len(payloads)!=50 or len(originals['ordinary']['fixtures'])!=40:raise ValueError('Fixed 40/50 input sets required')
        # This probe never changes queue/parallelism. Guarantee all 50 could
        # fit even without a consumer; avoid deliberate refusal in preparation.
        if min(initial['parameters']['global_queue'],initial['parameters']['project_queue'])<50:raise RuntimeError('Baseline diagnostic queue budget insufficient')
        guard=ResourceGuard(initial['workers']);guard.check(probe.resources(initial),guard.controller(),time.monotonic())
        with httpx.Client(base_url=c['base_url'],headers={'Authorization':'Bearer '+read_secret(c['customer_key_file'])},
                timeout=15,trust_env=False,follow_redirects=False,transport=httpx.HTTPTransport(retries=0,verify=c['ca_file'])) as client:
            mapping={}
            for row in originals['mixed']['fixtures']:
                data,media=payloads[row['id']];emit({'kind':'upload_intent','image':row['id'],'sha256':row['sha256']})
                response=client.post('/maas/v1/artifacts',content=data,headers={'Content-Type':media})
                if response.status_code!=201:raise RuntimeError('Input upload failed; inspect intent without replay')
                ident=response.json()['id'];emit({'kind':'upload_response','image':row['id'],'artifact_id':ident})
                mapping[artifact_id(row)]=ident
            receipt={'state':'COMPLETE','profile_id':c['profile_id'],'claim_proof_sha256':file_sha(config['claim_proof_file'])}
            for kind,old in originals.items():
                new=copy.deepcopy(old)
                for row in new['fixtures']:row['input']['image']['artifact_id']=mapping[artifact_id(row)]
                oldq=read_private(c['expected_text_file'] if kind=='ordinary' else claim['mixed_quality_file']);newq=copy.deepcopy(oldq);newq['input_sha256']=digest(new)
                validate_pair(old,new,oldq,newq)
                f=root/(kind+'-fixtures-private.json');q=root/(kind+'-quality-private.json');save_private(f,new);save_private(q,newq)
                receipt[kind]={'source_sha256':digest(old),'fixture_file':str(f),'sha256':digest(new),'quality_file':str(q),'quality_file_sha256':file_sha(q)}
            receipt_file=root/'refresh-private.json';save_private(receipt_file,receipt)
            refreshed={**config,'fixture_refresh_file':str(receipt_file),'fixture_refresh_sha256':file_sha(receipt_file)}
            _,manifests=load_refresh(refreshed,c,claim);finish_by=time.time()+21600;require_inputs(o,manifests,finish_by)
            cfg={**c,'workload_fixture_file':receipt['mixed']['fixture_file'],'workload_sha256':receipt['mixed']['sha256'],'expected_text_file':receipt['mixed']['quality_file']}
            fixtures,_=prepared_fixtures(cfg,originals['ordinary']);backend=TaskBackend(client,o,fixtures)
            backend.prepare();accepted=[]
            for n in range(50):
                fence.check(probe.capture()) if n%10==0 else None
                emit({'kind':'task_intent','sequence':n});ident=backend.submit(n);accepted.append(ident);emit({'kind':'task_accepted','sequence':n,'task_id':ident})
            until=time.monotonic()+180
            while True:
                activity=backend.snapshot()
                if activity['unknown'] or activity['sync_unknown'] or activity['paused']:raise RuntimeError('Diagnostic activity drift')
                rows=backend.reconcile(accepted)
                if any(r['state'] in ('FAILED','CANCELLED','CANCEL_REQUESTED') for r in rows):raise RuntimeError('Diagnostic task failed')
                if len(rows)==50 and all(r['state']=='SUCCEEDED' for r in rows):break
                if time.monotonic()>until:raise RuntimeError('Diagnostic drain deadline')
                fence.check(probe.capture());guard.check(probe.resources(initial),guard.controller(),time.monotonic());time.sleep(.5)
            if len(set(accepted))!=50 or {r['id'] for r in rows}!=set(accepted):raise RuntimeError('Diagnostic identity mismatch')
            if any(r['attempts']!=1 or r['events']!=1 for r in rows):raise RuntimeError('Diagnostic attempt/metering mismatch')
            verified=backend.verify_results(rows)
            with o.lock:
                sizes=o.connection.execute('SELECT t.idempotency_key,a.size FROM tasks t JOIN artifacts a ON a.id=t.result_artifact_id WHERE t.project=%s AND starts_with(t.idempotency_key,%s)',o.scope[:2]).fetchall()
            if len(sizes)!=50:raise RuntimeError('Diagnostic size evidence incomplete')
            ordinary_sizes=[r['size'] for r in sizes if int(r['idempotency_key'].rsplit(':',1)[1])<40]
            forecast=forecast_bytes(rate,sum(ordinary_sizes)/40,sum(r['size'] for r in sizes)/50)
            storage=live_storage(o);fence.check(probe.capture());state=o.snapshot()
            if any(state[k] for k in ('global_queued','running','sync_running','unknown','sync_unknown','paused')):raise RuntimeError('Preparation did not finish idle')
            result={'state':'READY','scope':'PREPARATION_ONLY_NOT_CONFIRMATION','prepared_at_epoch':time.time(),'finish_by_epoch':finish_by,
                'fixture_refresh_sha256':file_sha(receipt_file),'forecast':forecast,'storage':storage,
                'ordinary_mean_result_bytes':sum(ordinary_sizes)/40,'mixed_mean_result_bytes':sum(r['size'] for r in sizes)/50,
                'reference_mu_proof_sha256':file_sha(config['reference_mu_proof_file']),
                'runtime_fixed_profile_sha256':digest(fixed_profile(initial)),
                'probe':{'accepted':50,'successful_once':50,'verified_results':verified['downloaded_results']},'runtime_unchanged':True,'automatic_replay':False}
            try:
                require_budget(storage['current_bytes'],forecast['total_bytes'],storage['quota_bytes'],storage['disk_free_bytes'],storage['min_free_bytes'])
                require_budget(storage['global_current_bytes'],forecast['total_bytes'],storage['global_quota_bytes'],storage['disk_free_bytes'],storage['min_free_bytes'])
            except RuntimeError:result['state']='BLOCKED_STORAGE';result['reason']='Whole T6 forecast exceeds reserved quota or disk budget; no automatic cleanup/quota change'
            save_private(root/'reservation-private.json',result);save_private(root/'after-private.json',probe.capture())
            print(json.dumps({'state':result['state'],'probe':result['probe'],'forecast':forecast,'storage':storage,'runtime_unchanged':True}),flush=True)
            return result
    except BaseException as exc:
        save_private(root/'interrupted-private.json',{'state':'INTERRUPTED_REQUIRES_INSPECTION','error_class':type(exc).__name__,'automatic_replay':False});raise
    finally:log.close();o.close()


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--config',required=True);p.add_argument('--execute',action='store_true');a=p.parse_args()
    if a.execute:
        install_stop_handlers();run(read_private(a.config))
    else:print(json.dumps({'state':'DRY_RUN','execution':False}))
