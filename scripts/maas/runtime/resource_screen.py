"""One-shot 4/6/8 resident-replica mixed resource screen, no formal T6 claim."""
import argparse
import hashlib
import json
import os
import re
from pathlib import Path
import sys
import threading
import time
sys.path.insert(0,str(Path(__file__).resolve().parents[3]))
import httpx
from maas_bench.snapshot import read_private,read_secret,save_private,replace_private,RuntimeFence,digest
from maas_bench.runtime import ControllerProbe
from maas_bench.controller import PilotController
from maas_bench.backend import DatabaseObserver,TaskBackend
from maas_bench.fixtures import prepared_fixtures
from maas_bench.resources import ResourceGuard,observe_resources
from maas_bench.contracts import Parameters
from maas_bench.phase import proof_entry
from maas_bench.experiment import fresh_namespace,check_runtime
from maas_bench.t6_preparation import (load_refresh,workload_extra,require_inputs,
    live_storage,require_budget,install_stop_handlers)
from maas_bench.resource_screen import points,memory_plateau,configured_runner


def run(config):
    if config['diagnostic_kind']!='CONCURRENT_RESOURCE_SCREEN_NOT_T6_PROOF':raise ValueError('Explicit diagnostic scope required')
    if not re.fullmatch(r'[a-f0-9]{64}',config.get('private_mac_backup_receipt_sha256','')):raise ValueError('Private Mac backup receipt required')
    c=read_private(config['template_file']);claim,_=proof_entry(config['claim_proof_file'],'T5-CLAIM',c)
    extra=workload_extra(config,c,claim,'mixed');_,manifests=load_refresh(config,c,claim)
    root=Path(config['output_directory']);root.mkdir(mode=0o700,exist_ok=False)
    control=DatabaseObserver(read_secret(c['database_dsn_file']),c['project'],c['logical_model'],c['revision'],'e3-screen-controller-20261031:')
    probe=ControllerProbe(c['source_root'],c['model_id'],c['profile_id'],claim['input_sha256'])
    current=probe.capture();RuntimeFence(read_private(config['expected_runtime_file'])).check(current);check_runtime(c,current)
    save_private(root/'before-private.json',current)
    controller=PilotController(probe,control,root/'operations',experiment=c)
    status={'state':'PREPARING','scope':config['diagnostic_kind'],'points':[],
        'planned_parameters':points(),'formal_T6_proof':False,'production_promoted':False,
        'fixture_refresh_sha256':config['fixture_refresh_sha256'],'input_sha256':claim['input_sha256']}
    journal=root/'status-private.json';save_private(journal,status);finish_by=time.time()+7200
    try:
        require_inputs(control,manifests,finish_by);storage=live_storage(control)
        # Explicit conservative diagnostic reservation, not a T6 reservation.
        reserve=256*1024**2
        for now,quota in ((storage['current_bytes'],storage['quota_bytes']),
                          (storage['global_current_bytes'],storage['global_quota_bytes'])):
            require_budget(now,reserve,quota,storage['disk_free_bytes'],storage['min_free_bytes'])
        for index,params in enumerate(points()):
            if time.time()+600>finish_by:raise RuntimeError('Resource screening reservation expired')
            tag=config['run_tag'];run_id='screen-20261031-'+str(index)+'-'+tag
            folder=root/('point-'+str(index));fresh_namespace(c,control,run_id,[folder]);require_inputs(control,manifests,finish_by)
            folder.mkdir(mode=0o700);status.update(state='RUNNING',active_point=index);replace_private(journal,status)
            applied=controller.change(Parameters(**params));save_private(folder/'before-private.json',applied);fence=RuntimeFence(applied)
            cfg={**c,**extra,'run_id':run_id,'parameters':params,'snapshot_file':str(folder/'before-private.json'),
                 'scope':config['diagnostic_kind']};save_private(folder/'config-private.json',cfg)
            observer=DatabaseObserver(read_secret(c['database_dsn_file']),c['project'],c['logical_model'],c['revision'],'e3-'+run_id+':')
            resources=ResourceGuard(applied['workers']);records=[];halt=threading.Event();watcher=None;loglock=threading.Lock()
            fd=os.open(folder/'events-private.jsonl',os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600);log=os.fdopen(fd,'w')
            def emit(row):
                with loglock:
                    log.write(json.dumps(row)+'\n');log.flush()
                    if row['kind']!='sample':os.fsync(log.fileno())
                    if row['kind']=='resources':records.append(row)
            try:
                with httpx.Client(base_url=c['base_url'],headers={'Authorization':'Bearer '+read_secret(c['customer_key_file'])},timeout=15,
                    trust_env=False,follow_redirects=False,transport=httpx.HTTPTransport(retries=0,verify=c['ca_file'])) as client:
                    fixtures,_=prepared_fixtures(cfg,read_private(c['fixture_file']));backend=TaskBackend(client,observer,fixtures);backend.prepare()
                    runner=configured_runner(backend,params,applied,emit)
                    def watch():
                        try:
                            while not halt.is_set():
                                fence.check(probe.capture());observe_resources(resources,probe,applied,emit);halt.wait(.5)
                        except Exception as exc:
                            emit({'kind':'guard_failed','error_class':type(exc).__name__,'private_message':str(exc)});runner._fail(exc)
                    watcher=threading.Thread(target=watch);watcher.start();summary=runner.run();halt.set();watcher.join(30)
                    save_private(folder/'summary-private.json',summary)
                    if watcher.is_alive():raise RuntimeError('Screen resource watcher did not stop')
                    if summary['state']!='COMPLETE' or runner.failure:raise RuntimeError('Resource screen interrupted; inspect original readings and intents without replay')
                    fence.check(probe.capture());controller.quiet()
                    # Observe retained resident memory after all accepted work
                    # and verification, without creating more inference tasks.
                    idle_until=time.monotonic()+60
                    while time.monotonic()<idle_until:
                        fence.check(probe.capture());observe_resources(resources,probe,applied,emit);time.sleep(.5)
                    formal_end=max(x['at_monotonic'] for x in runner.samples if x['measuring'])
                    plateau=memory_plateau(records,applied['workers'],formal_end)
                    row={'point':index,'parameters':params,'state':'OBSERVED_SAFE_IN_BOUNDED_SCREEN',
                        'accepted':len(summary['accepted_task_ids']),'measurement_seconds':summary['measurement_seconds'],
                        'throughput_per_second':summary['throughput_per_second'],'supply':summary['supply'],
                        'card_memory':plateau,'idle_seconds':60,'idle_last_card_reading':records[-1]['workers'],
                        'quality_verified':True,'per_process_peak_proven':False}
                    save_private(folder/'result-private.json',row)
                    if not all(x['plateau_observed'] for x in plateau):raise RuntimeError('No last-minute memory plateau; stop higher tiers')
                    status['points'].append(row);replace_private(journal,status)
            finally:
                halt.set()
                if watcher:watcher.join(30)
                observer.close();log.close()
        restored=controller.restore();controller.quiet();save_private(root/'after-private.json',restored)
        status.update(state='COMPLETE',restored_parameters=restored['parameters'],restored_generation=restored['generation'],
            qualification_scope='180s saturated mixed workload plus 60s idle per tier, native aggregate sampled evidence only',
            per_process_peak_proven=False,formal_T6_proof=False,vm_worker_docker_daemon_restarts=0)
        replace_private(journal,status);return status
    except BaseException as exc:
        status.update(state='INTERRUPTED_REQUIRES_INSPECTION',error_class=type(exc).__name__);replace_private(journal,status);raise
    finally:control.close()


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--config',required=True);p.add_argument('--execute',action='store_true');a=p.parse_args()
    if a.execute:
        install_stop_handlers();s=run(read_private(a.config));print(json.dumps({'state':s['state'],'points':len(s['points']),'formal_T6_proof':False}))
    else:print(json.dumps({'state':'DRY_RUN','execution':False}))
