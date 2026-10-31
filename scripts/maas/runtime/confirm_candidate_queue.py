"""Real Q25 confirmation and contiguous HTTP overload recovery, no queue search."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import sys
import time
sys.path.insert(0,str(Path(__file__).resolve().parents[3]))
from maas_bench.snapshot import read_private,read_secret,save_private,replace_private,RuntimeFence,digest
from maas_bench.runtime import ControllerProbe
from maas_bench.controller import PilotController
from maas_bench.claim_transition import validate_evidence,change_claim
from maas_bench.contracts import Parameters
from maas_bench.backend import DatabaseObserver
from maas_bench.candidate_queue import PROTOCOL,CASE,qualification,planned,compatible_runtime
from maas_bench.queue_policy import load_policy,assess
from maas_bench.arrival_point import execute_arrivals
from maas_bench.phase import proof_entry
from maas_bench.experiment import fresh_namespace,check_runtime
from maas_bench.t6_preparation import load_refresh,workload_extra,require_inputs,live_storage,require_budget,install_stop_handlers


def bound(config,key):
    p=Path(config[key])
    if hashlib.sha256(p.read_bytes()).hexdigest()!=config[key+'_sha256']:raise ValueError('Candidate evidence changed')
    return read_private(p)


def run(config):
    if config.get('scope')!='Q25_CONFIRMATION_AND_HTTP_RECOVERY_NOT_T6':raise ValueError('Explicit Q25 confirmation scope required')
    if not re.fullmatch(r'[a-f0-9]{64}',config.get('private_mac_backup_receipt_sha256','')):raise ValueError('Private Mac backup receipt required')
    if not re.fullmatch(r'[a-z0-9-]{1,10}',config['run_tag']):raise ValueError('Bounded fresh attempt tag required')
    c=read_private(config['template_file']);claim,_=proof_entry(config['claim_proof_file'],'T5-CLAIM',c)
    _,manifests=load_refresh(config,c,claim);policy=load_policy(c)
    evidence={k:config[k] for k in ('candidate_mu_file','candidate_mu_sha256','candidate_mu_points_root','candidate_runtime_file','candidate_runtime_sha256')}
    proof=qualification({**c,**evidence});source_path=Path(config['candidate_runtime_file'])
    if hashlib.sha256(source_path.read_bytes()).hexdigest()!=config['candidate_runtime_sha256']:raise ValueError('Capacity runtime changed')
    source=read_private(source_path)
    probe=ControllerProbe(c['source_root'],c['model_id'],c['profile_id'],claim['input_sha256']);current=probe.capture()
    RuntimeFence(read_private(config['expected_runtime_file'])).check(current);check_runtime(c,current);compatible_runtime(source,current)
    target=validate_evidence(bound(config,'profile_receipt_file'),bound(config,'native_fit_file'),bound(config,'source_profile_snapshot_file'),current)
    if target.to_dict()!=proof['parameters']:raise ValueError('Resource authorization and capacity tuple differ')
    root=Path(config['output_directory']);root.mkdir(mode=0o700,exist_ok=False);save_private(root/'driver-pid-private.json',{'pid':os.getpid()})
    observer=DatabaseObserver(read_secret(c['database_dsn_file']),c['project'],c['logical_model'],c['revision'],'e3-c35-queue-controller-20261031:')
    controller=PilotController(probe,observer,root/'operations',experiment=c);save_private(root/'before-private.json',current)
    points=planned(proof);selection={'protocol':PROTOCOL,'points':points,'candidate_mu_sha256':config['candidate_mu_sha256'],
        'qos_policy_sha256':policy['_sha256'],'workload_sha256':{k:digest(v) for k,v in manifests.items()}}
    sel=root/'selection-private.json';save_private(sel,selection);sel_sha=hashlib.sha256(sel.read_bytes()).hexdigest()
    status={'state':'PREPARING','case_id':CASE,'protocol':PROTOCOL,'profile_id':c['profile_id'],'parameters':target.to_dict(),
        'reference_mu':proof['reference_mu'],'candidate_mu_sha256':config['candidate_mu_sha256'],'planned_points':points,'points':[],
        'queue_search_performed':False,'formal_T6_proof':False,'production_promoted':False}
    journal=root/'status-private.json';save_private(journal,status);deadline=time.time()+7200
    log=os.fdopen(os.open(root/'transition-resources-private.jsonl',os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600),'w')
    def emit(row):log.write(json.dumps(row)+'\n');log.flush();os.fsync(log.fileno())
    try:
        require_inputs(observer,manifests,deadline);storage=live_storage(observer);forecast=1024**3
        for used,quota in ((storage['current_bytes'],storage['quota_bytes']),(storage['global_current_bytes'],storage['global_quota_bytes'])):
            require_budget(used,forecast,quota,storage['disk_free_bytes'],storage['min_free_bytes'])
        status['state']='APPLYING';replace_private(journal,status)
        change_claim(controller,target.vram_claim_bytes,target.replicas,evidence=target,emit=emit)
        applied=controller.change(target);save_private(root/'applied-before-private.json',applied);compatible_runtime(source,applied)
        for i,p in enumerate(points):
            if time.time()+300>deadline:raise RuntimeError('Insufficient remaining Q25 reservation')
            require_inputs(observer,manifests,deadline);controller.quiet();RuntimeFence(controller.expected).check(probe.capture());controller.check_fixed(probe.capture())
            if p.get('phase_kind')=='recovery':
                prior=status['points'][-1]
                if prior['point_id']!=p['workload']+'-edge-1.2' or prior['queue_full_rejections']<=0:raise RuntimeError('No contiguous observed overload for recovery')
            run_id='cq35-20261031-'+str(i)+'-'+config['run_tag'];folder=root/('point-'+str(i));fresh_namespace(c,observer,run_id,[folder]);folder.mkdir(mode=0o700)
            snap=folder/'before-private.json';save_private(snap,probe.capture())
            cfg={**c,**workload_extra(config,c,claim,p['workload']),**evidence,'candidate_queue_protocol':PROTOCOL,'case_id':CASE,'point_id':p['point_id'],
                'parameters':target.to_dict(),'run_id':run_id,'output_parent':str(folder),'snapshot_file':str(snap),'selection_file':str(sel),'selection_sha256':sel_sha,
                'load_mode':p['mode'],'arrival_policy':p['arrival_policy'],'qos_class':p['qos_class'],'workload':p['workload']}
            for key in ('qos_repeat','phase_kind'):
                cfg.pop(key,None)
                if key in p:cfg[key]=p[key]
            save_private(folder/'config-private.json',cfg);status.update(state='RUNNING',active_point=i);replace_private(journal,status)
            result=execute_arrivals(cfg);result['qos']=assess(result,policy)
            if p['mode']=='independent_arrivals' and result['query_probe_count']<=0:raise RuntimeError('Missing real HTTP query pressure evidence')
            if p.get('phase_kind')=='recovery' and (result['accepted']!=3 or result['queue_full_rejections']):raise RuntimeError('Recovery probes not all accepted')
            row={k:v for k,v in result.items() if k not in ('accepted_task_ids','download_verification')};row['workload']=p['workload'];row['source_summary_sha256']=hashlib.sha256((folder/run_id/'summary.json').read_bytes()).hexdigest()
            if p.get('phase_kind')=='recovery':row.update(recovery_after=points[i-1]['point_id'],configuration_changed_between_overload_and_probe=False)
            status['points'].append(row);replace_private(journal,status)
            if not result['qos']['passed']:raise RuntimeError('Q25 does not satisfy frozen scenario QoS; inspect without parameter tuning')
        status['state']='RESTORING';replace_private(journal,status);original=controller.original['parameters']
        change_claim(controller,original['vram_claim_bytes'],original['replicas'],restoring=True,emit=emit)
        after=controller.change(Parameters(**original));controller.quiet();save_private(root/'after-private.json',after)
        status.update(state='COMPLETE',queue_qualified=True,http_overload_recovery_qualified=True,
            restored_parameters=after['parameters'],restored_generation=after['generation'],two_hour_gate_passed=False,
            maximum_http_capacity_proven=False,vm_worker_docker_daemon_restarts=0)
        replace_private(journal,status);return status
    except BaseException as exc:
        status.update(state='INTERRUPTED_REQUIRES_INSPECTION',error_class=type(exc).__name__);replace_private(journal,status);raise
    finally:observer.close();log.close()


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--config',required=True);p.add_argument('--execute',action='store_true');a=p.parse_args()
    if a.execute:
        install_stop_handlers();s=run(read_private(a.config));print(json.dumps({'state':s['state'],'points':len(s['points']),'queue_qualified':s['queue_qualified']}))
    else:print(json.dumps({'state':'DRY_RUN','execution':False}))
