"""T3 shared execution slots for two screened profiles; no automatic promotion."""
import argparse
import hashlib
import json
import math
import re
from pathlib import Path
import sys

sys.path.insert(0,str(Path(__file__).resolve().parents[3]))
from maas_bench.backend import DatabaseObserver
from maas_bench.contracts import Parameters
from maas_bench.controller import PilotController
from maas_bench.runtime import ControllerProbe
from maas_bench.snapshot import read_private,read_secret,save_private,digest,replace_private
from maas_bench.__main__ import execute
from maas_bench.sync_point import execute_sync
from benchmark_executors import percentile
from maas_bench.experiment import stamp,check_proof,check_runtime,fresh_namespace



def planned(candidates):
    points=[]
    if not 1<=len(candidates)<=2:raise ValueError('One or two screened profiles required')
    for candidate in sorted(candidates,key=lambda r:(r['parameters']['replicas'],r['parameters']['executors'])):
        base=candidate['parameters'];Parameters(**base)
        for global_limit in (4,6,8):
            for project_limit in dict.fromkeys((global_limit,math.ceil(global_limit/2),2)):
                for mode in ('async_saturation','sync_prepared','mixed_20','mixed_50'):
                    params={**base,'global_limit':global_limit,'project_limit':project_limit}
                    point_id='T3-R'+str(base['replicas'])+'-E'+str(base['executors'])+'-G'+str(global_limit)+'-P'+str(project_limit)+'-'+mode
                    points.append({'point_id':point_id,'parameters':params,'mode':mode,'reference_rate':candidate['screening_throughput']})
    if len({p['point_id'] for p in points})!=len(points):raise ValueError('Duplicate screened profile')
    return points


def run(config):
    proof_file=Path(config['async_proof_file']);raw=proof_file.read_bytes()
    if hashlib.sha256(raw).hexdigest()!=config['async_proof_sha256']:raise ValueError('T2 proof changed')
    proof=json.loads(raw)
    if proof['case_id']!='T2-ASYNC' or proof['state']!='COMPLETE':raise ValueError('Completed T2 required')
    output=Path(config['output_directory']);output.mkdir(mode=0o700,exist_ok=False)
    tag=config.get('run_tag','main')
    if not re.fullmatch(r'[a-z0-9-]{1,10}',tag):raise ValueError('Bounded unique attempt tag required')
    template=read_private(config['template_file']);template.update(expected_text_file=config['expected_text_file'],raw_manifest_file=config['raw_manifest_file'])
    if proof['profile_id']!=template['profile_id'] or proof['input_sha256']!=digest(read_private(template['fixture_file'])):raise ValueError('T2/input profile mismatch')
    check_proof(proof,template);context=stamp(template)
    points=planned(proof['screened_candidates'])
    selection={'case_id':'T3-LIMITS','profile_id':proof['profile_id'],'input_sha256':proof['input_sha256'],'points':points,**context}
    selection_file=output/'selection-private.json';save_private(selection_file,selection)
    observer=DatabaseObserver(read_secret(template['database_dsn_file']),template['project'],template['logical_model'],template['revision'],'e3-t3-controller-20261029:')
    probe=ControllerProbe(template['source_root'],template['model_id'],template['profile_id'],proof['input_sha256'])
    controller=PilotController(probe,observer,output/'operations',experiment=template)
    result={'case_id':'T3-LIMITS','profile_id':proof['profile_id'],'input_sha256':proof['input_sha256'],'state':'RUNNING','points':[],'planned_points':len(points),**context}
    replace_private(output/'status.json',result)
    try:
        for number,point in enumerate(points):
            target=Parameters(**point['parameters'])
            snapshot_file=output/(str(number)+'-before-private.json')
            run_id='t3-20261029-'+tag+'-'+str(number).zfill(3)+'-'+point['mode'].replace('_','-')
            cfg={**template,'case_id':'T3-LIMITS','point_id':point['point_id'],'run_id':run_id,'load_mode':point['mode'],
                'parameters':target.to_dict(),'snapshot_file':str(snapshot_file),'selection_file':str(selection_file),
                'selection_sha256':hashlib.sha256(selection_file.read_bytes()).hexdigest(),
                'completed_stages':{'T2-ASYNC':{'state':'COMPLETE','proof_file':str(proof_file),'proof_sha256':config['async_proof_sha256']}}}
            if point['mode'].startswith('mixed_'):cfg['sync_rate']=point['reference_rate']*(.2 if point['mode']=='mixed_20' else .5)
            fresh_namespace(template,observer,run_id,[snapshot_file,output/(str(number)+'-config-private.json'),Path(template['output_parent'])/run_id])
            if template.get('matrix_file'):
                from maas_bench.matrix import validate_execution
                validate_execution(cfg,proof['input_sha256'])
            snapshot=controller.change(target);save_private(snapshot_file,snapshot)
            save_private(output/(str(number)+'-config-private.json'),cfg)
            if point['mode']=='sync_prepared':data=execute_sync(cfg)
            else:
                value=execute(cfg)
                if value['state']!='COMPLETE':raise RuntimeError('Invalid T3 async/mixed point')
                data=read_private(Path(cfg['output_parent'])/run_id/'summary.json')
            controller.check_fixed(probe.capture());controller.quiet()
            row={'point_id':point['point_id'],'mode':point['mode'],'parameters':target.to_dict(),'state':'COMPLETE',
                'measurement_seconds':data['measurement_seconds'],'measured_successes':data['measured_successes'],
                'throughput_per_second':data['throughput_per_second']}
            if 'supply' in data:row['supply']=data['supply']
            if 'auxiliary' in data:
                records=data['auxiliary']['records'];a=data['window_start_db_epoch'];b=data['window_end_db_epoch']
                measured=[r for r in records if a<=r['finished_epoch']<=b]
                successes=[r for r in measured if r['http_status']==200]
                row.update(sync_offered_rate=cfg['sync_rate'],sync_successes=len(successes),
                    sync_rejections=sum(r['http_status']==429 for r in measured),
                    sync_success_p95_seconds=percentile([r['seconds'] for r in successes],.95),
                    combined_successes=len(successes)+data['measured_successes'],
                    combined_throughput=(len(successes)+data['measured_successes'])/data['measurement_seconds'],
                    sync_scope='HTTP responses in common epoch window; all-project-token usage reconciliation includes tail')
            elif point['mode']=='sync_prepared':
                values=[r['outcome']['seconds'] for r in data['records'] if r['completed_offset']<=data['measurement_seconds']]
                row.update(sync_success_p95_seconds=percentile(values,.95),sync_success_p99_seconds=percentile(values,.99))
            else:
                with observer.lock:
                    timings=observer.connection.execute("""SELECT extract(epoch FROM a.started_at-t.created_at)::float8 waiting,
                      extract(epoch FROM a.ended_at-a.started_at)::float8 executing FROM tasks t
                      JOIN task_attempts a ON a.task_id=t.id AND a.attempt=t.attempts
                      WHERE t.project=%s AND starts_with(t.idempotency_key,%s) AND t.state='SUCCEEDED'""",(template['project'],'e3-'+run_id+':')).fetchall()
                row.update(all_accepted_wait_p95=percentile([r['waiting'] for r in timings],.95),
                    all_accepted_execution_p95=percentile([r['executing'] for r in timings],.95))
            result['points'].append(row);replace_private(output/'status.json',result)
            print(json.dumps(row),flush=True)
        result['state']='RESTORING';replace_private(output/'status.json',result)
        restored=controller.restore();result.update(state='COMPLETE',restored_parameters=restored['parameters'],restored_generation=restored['generation'])
        replace_private(output/'status.json',result)
        print(json.dumps({'case_id':'T3-LIMITS','state':'COMPLETE','points':len(result['points'])}),flush=True)
    except BaseException as exc:
        result.update(state='INTERRUPTED_REQUIRES_INSPECTION',error_class=type(exc).__name__);replace_private(output/'status.json',result);raise
    finally:observer.close()


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--config',required=True);p.add_argument('--execute',action='store_true');a=p.parse_args()
    if a.execute:run(read_private(a.config))
    else:print(json.dumps({'state':'DRY_RUN','execution':False}))
