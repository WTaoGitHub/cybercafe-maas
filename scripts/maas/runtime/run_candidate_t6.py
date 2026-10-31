"""One-shot evidence-bound C35 formal confirmation and full segmented T6."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import statistics
import sys
import time
sys.path.insert(0,str(Path(__file__).resolve().parents[3]))
from maas_bench.snapshot import read_private,read_secret,save_private,replace_private,RuntimeFence,digest
from maas_bench.candidate_t6 import PROTOCOL,CASE,bound,prerequisites,capacity_plan,capacity_rates,latency_plan,soak_plan
from maas_bench.candidate_queue import compatible_runtime
from maas_bench.controller import PilotController
from maas_bench.claim_transition import validate_evidence,change_claim
from maas_bench.runtime import ControllerProbe
from maas_bench.backend import DatabaseObserver
from maas_bench.contracts import Parameters
from maas_bench.experiment import check_runtime,fresh_namespace
from maas_bench.t6_preparation import load_refresh,workload_extra,require_inputs,live_storage,require_budget,install_stop_handlers
from maas_bench.phase import proof_entry
from maas_bench.queue_policy import load_policy,assess,conservative_mu
from maas_bench.resources import ResourceGuard,observe_resources
from maas_bench.resource_screen import memory_plateau
from maas_bench.__main__ import execute
from maas_bench.arrival_point import execute_arrivals


def apply(controller,params,target,emit):
    if controller.expected['parameters']['vram_claim_bytes']!=params['vram_claim_bytes']:
        restoring=params==controller.original['parameters']
        if not restoring and params!=target.to_dict():raise ValueError('Unauthorized claim target')
        change_claim(controller,params['vram_claim_bytes'],params['replicas'],evidence=target,restoring=restoring,emit=emit)
    if controller.expected['parameters']!=params:controller.change(Parameters(**params))
    controller.quiet();RuntimeFence(controller.expected).check(controller.probe.capture())


def resource_gate(folder,raw,controller,idle_seconds):
    records=[];end=None
    with (folder/'events.jsonl').open() as f:
        for line in f:
            r=json.loads(line)
            if r['kind']=='resources':records.append(r)
            if r['kind']=='sample' and r['measuring']:end=r['at_monotonic']
    if end is None:raise RuntimeError('Missing resource gate window')
    guard=ResourceGuard(controller.expected['workers']);until=time.monotonic()+idle_seconds
    with (folder.parent/'idle-resources-private.jsonl').open('x') as f:
        os.chmod(f.name,0o600)
        def emit(r):records.append(r);f.write(json.dumps(r)+'\n');f.flush()
        while time.monotonic()<until:
            RuntimeFence(controller.expected).check(controller.probe.capture());observe_resources(guard,controller.probe,controller.expected,emit);time.sleep(.5)
    return memory_plateau(records,controller.expected['workers'],end)


def run(config):
    if config.get('scope')!='C35_FORMAL_CONFIRMATION_AND_FULL_T6':raise ValueError('Explicit C35 full T6 scope required')
    if not re.fullmatch(r'[a-f0-9]{64}',config.get('private_mac_backup_receipt_sha256','')):raise ValueError('Mac private backup receipt required')
    if not re.fullmatch(r'[a-z0-9-]{1,10}',config['run_tag']):raise ValueError('Fresh bounded run tag required')
    c=read_private(config['template_file']);evidence={**c,**config};qualified=prerequisites(evidence)
    claim,_=proof_entry(config['claim_proof_file'],'T5-CLAIM',c);_,manifests=load_refresh(config,c,claim);policy=load_policy(c)
    original=bound(evidence,'original_runtime_file');source=bound(evidence,'candidate_runtime_file')
    probe=ControllerProbe(c['source_root'],c['model_id'],c['profile_id'],qualified['input_sha256']);current=probe.capture()
    RuntimeFence(original).check(current);check_runtime(c,current);compatible_runtime(source,current)
    target=validate_evidence(bound(evidence,'profile_receipt_file'),bound(evidence,'native_fit_file'),bound(evidence,'source_profile_snapshot_file'),current)
    if target.to_dict()!=qualified['parameters']:raise ValueError('Target resource authorization differs')
    manifest=bound(evidence,'source_manifest_file')
    if not all(hashlib.sha256((Path(c['source_root'])/name).read_bytes()).hexdigest()==sha for name,sha in manifest.items()):raise ValueError('Execution source drift')
    root=Path(config['output_directory']);root.mkdir(mode=0o700,exist_ok=False);save_private(root/'driver-pid-private.json',{'pid':os.getpid()});save_private(root/'before-private.json',current)
    observer=DatabaseObserver(read_secret(c['database_dsn_file']),c['project'],c['logical_model'],c['revision'],'e3-c35-t6-controller-20261031:',statement_ms=1000)
    controller=PilotController(probe,observer,root/'operations',experiment=c)
    status={'state':'PREPARING','case_id':CASE,'protocol':PROTOCOL,'parameters':target.to_dict(),'production_promoted':False,'automatic_resume':False,'points':[],'maintenance':[]}
    journal=root/'status-private.json';save_private(journal,status);deadline=time.time()+18000
    transition=os.fdopen(os.open(root/'transition-resources-private.jsonl',os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600),'w')
    def emit(r):transition.write(json.dumps(r)+'\n');transition.flush();os.fsync(transition.fileno())
    def selection(stage,points,extra=None):
        folder=root/stage;folder.mkdir(mode=0o700);s={'protocol':PROTOCOL,'stage':stage,'points':points,'queue_proof_sha256':config['queue_proof_file_sha256'],
            'workload_sha256':{k:digest(v) for k,v in manifests.items()},**(extra or {})};f=folder/'selection-private.json';save_private(f,s)
        return folder,{'selection_file':str(f),'selection_file_sha256':hashlib.sha256(f.read_bytes()).hexdigest()}
    def point(stage,folder,selection_config,index,p,extra=None):
        if time.time()+600>deadline:raise RuntimeError('Insufficient full T6 reserved lifetime')
        require_inputs(observer,manifests,deadline);apply(controller,p['parameters'],target,emit)
        run_id=f'c35t6-{stage}-{index}-{config["run_tag"]}';d=folder/('point-'+str(index));fresh_namespace(c,observer,run_id,[d]);d.mkdir(mode=0o700)
        snap=d/'before-private.json';save_private(snap,probe.capture())
        cfg={**c,**config,**(extra or {}),**selection_config,**workload_extra(config,c,claim,p['workload']),
            'candidate_t6_protocol':PROTOCOL,'case_id':CASE,'point_id':p['point_id'],'run_id':run_id,'output_parent':str(d),'snapshot_file':str(snap),
            'parameters':p['parameters'],'load_mode':p['mode'],'workload':p['workload']}
        for key in ('candidate_queue_protocol','arrival_policy','qos_class','qos_repeat','sync_rate'):cfg.pop(key,None)
        for key in ('arrival_policy','qos_class','sync_rate'):
            if key in p:cfg[key]=p[key]
        if p['mode'] in ('async_saturation','mixed_20'):cfg['policy']={'seconds':300,'min_successes':200,'max_seconds':300}
        save_private(d/'config-private.json',cfg);status.update(state='RUNNING',active_stage=stage,active_point=index,active_point_id=p['point_id']);replace_private(journal,status)
        if p['mode'] in ('async_saturation','mixed_20'):
            value=execute(cfg)
            if value['state']!='COMPLETE':raise RuntimeError('Formal saturated point incomplete')
            raw=read_private(d/run_id/'summary.json')
            row={k:raw[k] for k in ('state','measurement_seconds','measured_successes','throughput_per_second','supply','completion_witness')}
            row.update(accepted=len(raw['accepted_task_ids']),downloaded_and_text_verified=raw['result_verification']['downloaded_results'])
            if 'auxiliary' in raw:row['sync']={k:raw['auxiliary'][k] for k in ('offered_rate','successes','capacity_rejections','usage_events','unsent')}
            if row['measurement_seconds']<300:raise RuntimeError('Formal window shorter than 300 seconds')
        else:
            raw=execute_arrivals(cfg);qos=assess(raw,policy)
            row={k:v for k,v in raw.items() if k not in ('accepted_task_ids','download_verification')};row['qos']=qos
            if not qos['passed']:raise RuntimeError('Frozen T6 arrival QoS not met')
        row.update(**p,run_id=run_id)
        row['source_summary_sha256']=hashlib.sha256((d/run_id/'summary.json').read_bytes()).hexdigest()
        if p.get('role')=='resource':
            row['card_memory']=resource_gate(d/run_id,raw,controller,p['idle_seconds']);row['resource_qualified']=all(x['plateau_observed'] for x in row['card_memory'])
        f=d/'result-private.json';save_private(f,row);row={**row,'source_result_sha256':hashlib.sha256(f.read_bytes()).hexdigest()};status['points'].append({'stage':stage,**row});replace_private(journal,status)
        if p.get('role')=='resource' and not row['resource_qualified']:raise RuntimeError('Mixed last-minute memory plateau not met')
        return row
    try:
        controller.quiet();require_inputs(observer,manifests,deadline);storage=live_storage(observer)
        reserve=bound(evidence,'reservation_file')
        if reserve.get('state')!='READY' or reserve['original_runtime_sha256']!=config['original_runtime_file_sha256'] or reserve['fixture_refresh_sha256']!=config['fixture_refresh_sha256'] or not 0<reserve['soak_forecast_bytes']<=reserve['forecast_bytes']:raise ValueError('Full-cycle reservation not bound')
        for used,quota in ((storage['current_bytes'],storage['quota_bytes']),(storage['global_current_bytes'],storage['global_quota_bytes'])):require_budget(used,reserve['forecast_bytes'],quota,storage['disk_free_bytes'],storage['min_free_bytes'])
        save_private(root/'reservation-before-private.json',{'finish_by_epoch':deadline,'storage':storage,'forecast_bytes':reserve['forecast_bytes']})
        points=capacity_plan(original['parameters'],target.to_dict());folder,sel=selection('capacity',points);rows=[point('capacity',folder,sel,i,p) for i,p in enumerate(points)]
        cap={'state':'COMPLETE','case_id':'T6-C35-CAPACITY','parameters':target.to_dict(),'planned_points':points,'points':rows,'queue_proof_sha256':config['queue_proof_file_sha256']};cap['reference_mu']=capacity_rates(cap)
        baseline=[r for r in rows if r.get('role')=='baseline'];conservative_mu(baseline,original['parameters'])
        mean_base=statistics.mean(r['throughput_per_second'] for r in baseline);mean_new=statistics.mean(r['throughput_per_second'] for r in rows if r.get('role')=='candidate' and r['workload']=='ordinary')
        cap.update(baseline_mean_throughput=mean_base,candidate_mean_throughput=mean_new,observed_improvement_fraction=mean_new/mean_base-1)
        if mean_new<mean_base*1.05:raise RuntimeError('Candidate lacks predeclared five percent capacity benefit')
        f=folder/'proof-private.json';save_private(f,cap);capacity_binding={'capacity_proof_file':str(f),'capacity_proof_file_sha256':hashlib.sha256(f.read_bytes()).hexdigest()}
        points=latency_plan(target.to_dict(),cap['reference_mu']);folder,sel=selection('latency',points,{'capacity_proof_sha256':capacity_binding['capacity_proof_file_sha256']})
        rows=[point('latency',folder,sel,i,p,capacity_binding) for i,p in enumerate(points)]
        eligible=[r for r in rows if r['workload']=='ordinary' and not r['queue_full_rejections'] and r['total_p95']<=1.5 and r['total_p99']<=3]
        if not eligible:raise RuntimeError('Ordinary formal latency budget unmet at both common rates')
        confirmed={'state':'COMPLETE','case_id':'T6-C35-CONFIRM','parameters':target.to_dict(),'capacity_proof_sha256':capacity_binding['capacity_proof_file_sha256'],
            'planned_points':points,'points':rows,'reference_mu':cap['reference_mu'],'latency_qualified':True,'validated_ordinary_arrival_rate':max(r['target_rate'] for r in eligible),'production_promoted':False}
        f=folder/'proof-private.json';save_private(f,confirmed);soak_binding={**capacity_binding,'confirmation_proof_file':str(f),'confirmation_proof_file_sha256':hashlib.sha256(f.read_bytes()).hexdigest()}
        if deadline-time.time()<9000:raise RuntimeError('Insufficient reserved time for complete T6')
        require_inputs(observer,manifests,deadline);soak_storage=live_storage(observer)
        for used,quota in ((soak_storage['current_bytes'],soak_storage['quota_bytes']),(soak_storage['global_current_bytes'],soak_storage['global_quota_bytes'])):
            require_budget(used,reserve['soak_forecast_bytes'],quota,soak_storage['disk_free_bytes'],soak_storage['min_free_bytes'])
        save_private(root/'soak-reservation-private.json',{'storage':soak_storage,'forecast_bytes':reserve['soak_forecast_bytes'],'finish_by_epoch':deadline})
        points=soak_plan(target.to_dict(),cap['reference_mu']);folder,sel=selection('soak',points,{'capacity_proof_sha256':capacity_binding['capacity_proof_file_sha256']});started=time.monotonic();formal=0;soak_rows=[]
        for i,p in enumerate(points):
            if p.get('segment')==12:
                status.update(state='EXECUTOR_MAINTENANCE');replace_private(journal,status);before=controller.expected['generation'];began=time.monotonic()
                apply(controller,{**target.to_dict(),'executors':7},target,emit);apply(controller,target.to_dict(),target,emit)
                status['maintenance'].append({'from_executors':8,'temporary_executors':7,'restored_executors':8,'before_generation':before,'after_generation':controller.expected['generation'],'seconds':time.monotonic()-began,'unknown_replayed':False})
            row=point('soak',folder,sel,i,p,soak_binding);soak_rows.append(row)
            if 'segment' in p:formal+=row['measurement_seconds']
        elapsed=time.monotonic()-started
        if formal<7200 or elapsed<7200 or len(status['maintenance'])!=1:raise RuntimeError('Full T6 segment or maintenance coverage incomplete')
        status.update(state='RESTORING');replace_private(journal,status);apply(controller,original['parameters'],target,emit)
        after=probe.capture();save_private(root/'after-private.json',after)
        status.update(state='COMPLETE',capacity_proof_sha256=capacity_binding['capacity_proof_file_sha256'],confirmation_proof_sha256=soak_binding['confirmation_proof_file_sha256'],
            reference_mu=cap['reference_mu'],ordinary_capacity_improvement_fraction=cap['observed_improvement_fraction'],formal_segment_seconds=formal,soak_wall_seconds=elapsed,
            two_hour_gate_passed=True,restored_parameters=after['parameters'],restored_generation=after['generation'],vm_worker_docker_daemon_restarts=0,requests_replayed=0,sla_claim=False)
        replace_private(journal,status);return status
    except BaseException as exc:
        status.update(state='INTERRUPTED_REQUIRES_INSPECTION',error_class=type(exc).__name__);replace_private(journal,status);raise
    finally:observer.close();transition.close()


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--config',required=True);p.add_argument('--execute',action='store_true');a=p.parse_args()
    if a.execute:
        install_stop_handlers();s=run(read_private(a.config));print(json.dumps({'state':s['state'],'formal_segment_seconds':s['formal_segment_seconds'],'restored_generation':s['restored_generation']}))
    else:print(json.dumps({'state':'DRY_RUN','execution':False}))
