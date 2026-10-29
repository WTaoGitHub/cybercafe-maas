"""Dry planning is default; executing one unchanged async point is explicit."""
import argparse
import json
import os
from pathlib import Path
import re
import threading
import time
import resource
import sys

from . import matrix
from .contracts import Parameters
from .snapshot import RuntimeFence,digest,read_private,read_secret,save_private


def execute(config):
    """No config scaling, automatic restore, resume, or inference retries here."""
    import httpx
    from .backend import DatabaseObserver,TaskBackend
    from .runtime import ControllerProbe
    from .saturation import RunPolicy,SaturationRunner
    from .resources import ResourceGuard
    run_id=config['run_id']
    if not re.fullmatch(r'[a-z0-9-]{8,64}',run_id):raise ValueError('Fresh bounded run ID required')
    original=read_private(config['snapshot_file']);fence=RuntimeFence(original)
    fixture_data=read_private(config['fixture_file'])
    if digest(fixture_data)!=original['input_sha256']:raise ValueError('Input manifest changed')
    if original['profile_id']!=config['profile_id']:raise ValueError('Wrong model profile')
    parameters=Parameters(**config['parameters'])
    if original['parameters']!=parameters.to_dict():raise ValueError('Apply/verify parameters separately under maintenance before measurement')
    policy=RunPolicy(**config.get('policy',{}))
    if policy.seconds<60 or policy.min_successes<200:raise ValueError('Live formal windows require >=60 seconds AND >=200 successes')
    if policy.max_seconds>300:raise ValueError('Use an explicitly designed soak driver for longer windows')
    matrix.validate_execution(config,digest(fixture_data))
    # The operator keeps original full model/app.env/maintenance bindings on the
    # Mac first. This receipt is an explicit precondition, not automatic proof.
    if not re.fullmatch(r'[a-f0-9]{64}',config.get('private_mac_backup_receipt_sha256','')):
        raise ValueError('Record private Mac backup receipt before live execution')
    probe=ControllerProbe(config['source_root'],config['model_id'],config['profile_id'],digest(fixture_data))
    current=probe.capture();fence.check(current)
    folder=Path(config['output_parent'])/run_id;folder.mkdir(mode=0o700,parents=False,exist_ok=False)
    save_private(folder/'before.json',current)
    fd=os.open(folder/'events.jsonl',os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
    log=os.fdopen(fd,'w');loglock=threading.Lock()
    def emit(row):
        with loglock:
            log.write(json.dumps(row)+'\n');log.flush()
            if row['kind']!='sample':os.fsync(log.fileno())
    observer=None;watcher=None;halt=threading.Event()
    try:
        observer=DatabaseObserver(read_secret(config['database_dsn_file']),config['project'],config['logical_model'],config['revision'],'e3-'+run_id+':')
        initial=observer.snapshot()
        if any(initial[k] for k in ('global_queued','running','sync_running','unknown','sync_unknown')):
            raise RuntimeError('Unisolated active traffic or UNKNOWN at experiment start')
        with httpx.Client(base_url=config['base_url'],headers={'Authorization':'Bearer '+read_secret(config['customer_key_file'])},
            verify=config.get('ca_file',True),timeout=10,trust_env=False,follow_redirects=False,
            transport=httpx.HTTPTransport(retries=0,verify=config.get('ca_file',True))) as client:
            fixtures=fixture_data['fixtures']
            if config.get('expected_text_file'):
                quality=read_private(config['expected_text_file'])
                if quality['input_sha256']!=digest(fixture_data) or quality['profile_id']!=config['profile_id']:
                    raise ValueError('Different quality reference profile')
                fixtures=[{**f,'expected_text_sha256':quality['items'][f['id']]['text_sha256']} for f in fixtures]
            backend=TaskBackend(client,observer,fixtures);backend.prepare()
            runner=SaturationRunner(backend,parameters,policy,initial['generation'],emit)
            resources=ResourceGuard(current['workers'])
            def watch():
                try:
                    while not halt.is_set():
                        fence.check(probe.capture())
                        rows=probe.resources(current);controller=resources.controller();now=time.monotonic()
                        resources.check(rows,controller,now)
                        emit({'kind':'resources','at_monotonic':now,'workers':rows,'controller':controller})
                        halt.wait(.5)
                except Exception as exc:
                    emit({'kind':'guard_failed','error_class':type(exc).__name__,'private_message':str(exc)})
                    runner._fail(exc)
            watcher=threading.Thread(target=watch,name='bench-native-guard');watcher.start()
            cpu_before=time.process_time();run_started=time.monotonic()
            summary=runner.run()
            generator={'cpu_seconds':time.process_time()-cpu_before,'elapsed_seconds':time.monotonic()-run_started,
                'process_peak_rss_bytes':resource.getrusage(resource.RUSAGE_SELF).ru_maxrss*(1 if sys.platform=='darwin' else 1024)}
            halt.set();watcher.join(timeout=30)
            if watcher.is_alive():raise RuntimeError('Runtime guard did not stop')
            if runner.failure and summary['state']=='COMPLETE':raise runner.failure
            after=probe.capture();save_private(folder/'after.json',after);fence.check(after)
            summary.update(profile_id=config['profile_id'],input_sha256=digest(fixture_data),
                point_id=config['point_id'],generator=generator,configuration_changed_by_harness=False,restore_required=False)
            save_private(folder/'summary.json',summary)
            return {'run_id':run_id,'state':summary['state'],'measured_successes':summary.get('measured_successes'),
                'throughput_per_second':summary.get('throughput_per_second'),'private_evidence_directory':str(folder)}
    except BaseException as exc:
        save_private(folder/'interrupted.json',{'state':'INTERRUPTED_REQUIRES_INSPECTION','error_class':type(exc).__name__,
            'automatic_replay':False,'automatic_restore':False})
        raise
    finally:
        halt.set()
        if watcher:watcher.join(timeout=30)
        if observer:observer.close()
        log.close()


def main():
    p=argparse.ArgumentParser(description=__doc__);sub=p.add_subparsers(dest='command',required=True)
    plan=sub.add_parser('plan');plan.add_argument('--matrix',default=str(matrix.DEFAULT_MATRIX))
    for name in ('case-id','module','label'):plan.add_argument('--'+name,action='append',default=[])
    plan.add_argument('--ledger')
    capture=sub.add_parser('capture');capture.add_argument('--config',required=True);capture.add_argument('--out',required=True)
    run=sub.add_parser('run-async');run.add_argument('--config',required=True);run.add_argument('--execute',action='store_true')
    for name in ('case-id','module','label'):run.add_argument('--'+name,action='append',default=[])
    a=p.parse_args()
    if a.command=='plan':
        ledger=json.loads(Path(a.ledger).read_text()) if a.ledger else None
        result=matrix.plan(matrix.load(a.matrix),a.case_id,a.module,a.label,ledger)
    elif a.command=='capture':
        from .runtime import ControllerProbe
        c=read_private(a.config);fixtures=read_private(c['fixture_file'])
        value=ControllerProbe(c['source_root'],c['model_id'],c['profile_id'],digest(fixtures)).capture()
        save_private(a.out,value);result={'state':'CAPTURED_READ_ONLY','snapshot_sha256':digest(value)}
    else:
        c=read_private(a.config)
        if any((a.case_id,a.module,a.label)):
            selected=matrix.select(matrix.load(),a.case_id,a.module,a.label)
            if c['case_id'] not in {s['case_id'] for s in selected}:raise ValueError('Configured point excluded by selectors; no execution')
        if not a.execute:
            result={'state':'DRY_RUN','run_id':c['run_id'],'parameters':Parameters(**c['parameters']).to_dict(),
                'executes_inference':False,'changes_configuration':False}
        else:result=execute(c)
    print(json.dumps(result,indent=2))
    if a.command=='run-async' and a.execute and result['state']!='COMPLETE':return 2
    return 0


if __name__=='__main__':
    try:raise SystemExit(main())
    except Exception as exc:
        # Never print a DSN, credential, input payload, or private endpoint from
        # an exception repr. Detailed accepted IDs are already in private files.
        print(json.dumps({'state':'STOPPED','error_class':type(exc).__name__}));raise SystemExit(2)
