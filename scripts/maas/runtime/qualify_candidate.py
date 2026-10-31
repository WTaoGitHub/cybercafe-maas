"""Evidence-bound 3.5GiB candidate: container-cold, mixed resources and 2x3 MU."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import sys
import threading
import time
sys.path.insert(0,str(Path(__file__).resolve().parents[3]))
import httpx
from maas_bench.snapshot import read_private,read_secret,save_private,replace_private,RuntimeFence
from maas_bench.runtime import ControllerProbe
from maas_bench.controller import PilotController
from maas_bench.claim_transition import validate_evidence,change_claim
from maas_bench.candidate_qualification import planned,reference_rates
from maas_bench.backend import DatabaseObserver,TaskBackend
from maas_bench.fixtures import prepared_fixtures
from maas_bench.saturation import SaturationRunner,RunPolicy
from maas_bench.resources import ResourceGuard,observe_resources
from maas_bench.resource_screen import memory_plateau
from maas_bench.contracts import Parameters
from maas_bench.phase import proof_entry
from maas_bench.experiment import fresh_namespace,check_runtime
from maas_bench.t6_preparation import load_refresh,workload_extra,require_inputs,live_storage,require_budget,install_stop_handlers


def bound_file(config,key):
    p=Path(config[key])
    if hashlib.sha256(p.read_bytes()).hexdigest()!=config[key+'_sha256']:raise ValueError('Qualification evidence file changed')
    return read_private(p)


def point(config,c,claim,controller,target,plan,root,index,deadline):
    if time.time()+180>deadline:raise RuntimeError('Insufficient remaining qualification budget')
    _,manifests=load_refresh(config,c,claim);require_inputs(controller.observer,manifests,deadline)
    run_id='q35-20261031-'+str(index)+'-'+config['run_tag'];folder=root/('point-'+str(index))
    fresh_namespace(c,controller.observer,run_id,[folder]);folder.mkdir(mode=0o700)
    current=controller.probe.capture();controller.check_fixed(current);RuntimeFence(controller.expected).check(current);controller.quiet()
    if current['parameters']!=target.to_dict():raise RuntimeError('Wrong actual qualification tuple')
    save_private(folder/'before-private.json',current);fence=RuntimeFence(current)
    extra=workload_extra(config,c,claim,plan['workload']);cfg={**c,**extra,'run_id':run_id,'parameters':target.to_dict()};save_private(folder/'config-private.json',cfg)
    observer=DatabaseObserver(read_secret(c['database_dsn_file']),c['project'],c['logical_model'],c['revision'],'e3-'+run_id+':')
    resource=ResourceGuard(current['workers']);records=[];halt=threading.Event();thread=None;lock=threading.Lock()
    log=os.fdopen(os.open(folder/'events-private.jsonl',os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600),'w')
    def emit(row):
        with lock:
            log.write(json.dumps(row)+'\n');log.flush()
            if row['kind']!='sample':os.fsync(log.fileno())
            if row['kind']=='resources':records.append(row)
    try:
        with httpx.Client(base_url=c['base_url'],headers={'Authorization':'Bearer '+read_secret(c['customer_key_file'])},timeout=15,
            trust_env=False,follow_redirects=False,transport=httpx.HTTPTransport(retries=0,verify=c['ca_file'])) as client:
            fixtures,_=prepared_fixtures(cfg,read_private(c['fixture_file']));backend=TaskBackend(client,observer,fixtures);backend.prepare()
            runner=SaturationRunner(backend,target,RunPolicy(seconds=plan['seconds'],max_seconds=plan['seconds'],min_successes=200),current['generation'],emit)
            def watch():
                try:
                    while not halt.is_set():
                        fence.check(controller.probe.capture());observe_resources(resource,controller.probe,current,emit);halt.wait(.5)
                except Exception as exc:
                    emit({'kind':'guard_failed','error_class':type(exc).__name__,'private_message':str(exc)});runner._fail(exc)
            thread=threading.Thread(target=watch);thread.start();s=runner.run();halt.set();thread.join(30)
            save_private(folder/'summary-private.json',s)
            if thread.is_alive() or s['state']!='COMPLETE' or runner.failure:raise RuntimeError('Qualification interrupted; no replay')
            fence.check(controller.probe.capture());controller.quiet()
            if plan['idle_seconds']:
                until=time.monotonic()+plan['idle_seconds']
                while time.monotonic()<until:
                    fence.check(controller.probe.capture());observe_resources(resource,controller.probe,current,emit);time.sleep(.5)
            end=max(x['at_monotonic'] for x in runner.samples if x['measuring']);cards=memory_plateau(records,current['workers'],end)
            if plan['phase']=='resource' and not all(x['plateau_observed'] for x in cards):raise RuntimeError('Qualification lacks last-minute memory plateau')
            row={**plan,'state':'COMPLETE','parameters':target.to_dict(),'run_id':run_id,
                'measurement_seconds':s['measurement_seconds'],'measured_successes':s['measured_successes'],'throughput_per_second':s['throughput_per_second'],
                'supply':s['supply'],'accepted':len(s['accepted_task_ids']),'downloaded_and_text_verified':s['result_verification']['downloaded_results'],
                'card_memory':cards,'resource_qualified':plan['phase']=='resource','source_summary_sha256':hashlib.sha256((folder/'summary-private.json').read_bytes()).hexdigest()}
            save_private(folder/'result-private.json',row);return row
    finally:
        halt.set()
        if thread:thread.join(30)
        observer.close();log.close()


def run(config):
    if config.get('scope')!='NEW_CLAIM_RESOURCE_AND_MU_NOT_T6':raise ValueError('Explicit qualification scope required')
    if not re.fullmatch(r'[a-f0-9]{64}',config.get('private_mac_backup_receipt_sha256','')):raise ValueError('Private Mac backup receipt required')
    if not re.fullmatch(r'[a-z0-9-]{1,10}',config['run_tag']):raise ValueError('Fresh bounded run tag required')
    c=read_private(config['template_file']);claim,_=proof_entry(config['claim_proof_file'],'T5-CLAIM',c)
    _,manifests=load_refresh(config,c,claim)
    probe=ControllerProbe(c['source_root'],c['model_id'],c['profile_id'],claim['input_sha256']);current=probe.capture()
    RuntimeFence(read_private(config['expected_runtime_file'])).check(current);check_runtime(c,current)
    target=validate_evidence(bound_file(config,'profile_receipt_file'),bound_file(config,'native_fit_file'),bound_file(config,'source_profile_snapshot_file'),current)
    root=Path(config['output_directory']);root.mkdir(mode=0o700,exist_ok=False)
    save_private(root/'driver-pid-private.json',{'pid':os.getpid()})
    observer=DatabaseObserver(read_secret(c['database_dsn_file']),c['project'],c['logical_model'],c['revision'],'e3-qual35-controller-20261031:')
    controller=PilotController(probe,observer,root/'operations',experiment=c);save_private(root/'before-private.json',current)
    status={'state':'PREPARING','case_id':'CANDIDATE-QUALIFICATION-35','profile_id':c['profile_id'],'input_sha256':claim['input_sha256'],
        'parameters':target.to_dict(),'planned_points':planned(),'points':[],'formal_T6_proof':False,'production_promoted':False,
        'fixture_refresh_sha256':config['fixture_refresh_sha256']};journal=root/'status-private.json';save_private(journal,status)
    cold=os.fdopen(os.open(root/'transition-resources-private.jsonl',os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600),'w');deadline=time.time()+1100
    def emit(row):cold.write(json.dumps(row)+'\n');cold.flush();os.fsync(cold.fileno())
    try:
        require_inputs(observer,manifests,time.time()+3600);storage=live_storage(observer);reserve=512*1024**2
        for used,quota in ((storage['current_bytes'],storage['quota_bytes']),(storage['global_current_bytes'],storage['global_quota_bytes'])):
            require_budget(used,reserve,quota,storage['disk_free_bytes'],storage['min_free_bytes'])
        status['state']='COLD_START';replace_private(journal,status)
        change_claim(controller,target.vram_claim_bytes,target.replicas,evidence=target,emit=emit)
        applied=controller.change(target);save_private(root/'qualified-tuple-before-private.json',applied)
        native=probe.api('/v2/models/'+str(c['model_id'])+'/instances')['items'];save_private(root/'native-instances-during-private.json',native)
        for i,p in enumerate(planned()):
            status.update(state='RUNNING',active_point=i);replace_private(journal,status)
            row=point(config,c,claim,controller,target,p,root,i,deadline);status['points'].append(row);replace_private(journal,status)
        rates=reference_rates(status['points'],target.to_dict());status.update(state='RESTORING',reference_mu=rates);replace_private(journal,status)
        original=controller.original['parameters'];change_claim(controller,original['vram_claim_bytes'],original['replicas'],restoring=True,emit=emit)
        after=controller.change(Parameters(**original));controller.quiet();save_private(root/'after-private.json',after)
        status.update(state='COMPLETE',restored_parameters=after['parameters'],restored_generation=after['generation'],
            native_cold_scope='inference containers rebuilt; images/weights cached; no VM/worker/Docker daemon restart',
            absolute_peak_proven=False,queue_qualified=False,two_hour_gate_passed=False,vm_worker_docker_daemon_restarts=0)
        replace_private(journal,status);return status
    except BaseException as exc:
        status.update(state='INTERRUPTED_REQUIRES_INSPECTION',error_class=type(exc).__name__);replace_private(journal,status);raise
    finally:observer.close();cold.close()


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--config',required=True);p.add_argument('--execute',action='store_true');a=p.parse_args()
    if a.execute:
        install_stop_handlers();s=run(read_private(a.config));print(json.dumps({'state':s['state'],'points':len(s['points']),'reference_mu':s['reference_mu']}))
    else:print(json.dumps({'state':'DRY_RUN','execution':False}))
