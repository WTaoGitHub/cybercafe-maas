"""T2: guarded R/E matrix, timed backlog, and explicit original restoration."""
import argparse
import hashlib
import json
from pathlib import Path
import sys

sys.path.insert(0,str(Path(__file__).resolve().parents[3]))
from maas_bench.backend import DatabaseObserver
from maas_bench.contracts import Parameters
from maas_bench.controller import PilotController
from maas_bench.runtime import ControllerProbe
from maas_bench.snapshot import read_private,read_secret,save_private
from maas_bench.__main__ import execute
from benchmark_executors import atomic


def run(config):
    proof_file=Path(config['native_proof_file']);raw=proof_file.read_bytes()
    if hashlib.sha256(raw).hexdigest()!=config['native_proof_sha256']:raise ValueError('T1 proof changed')
    proof=json.loads(raw)
    if proof['case_id']!='T1-NATIVE' or proof['state']!='COMPLETE':raise ValueError('T1 completion required')
    template=read_private(config['template_file'])
    if proof['input_sha256']!=read_private(template['snapshot_file'])['input_sha256'] or proof['profile_id']!=template['profile_id']:
        raise ValueError('T1/input/profile mismatch')
    output=Path(config['output_directory']);output.mkdir(mode=0o700,exist_ok=False)
    observer=DatabaseObserver(read_secret(template['database_dsn_file']),template['project'],template['logical_model'],template['revision'],'e3-t2-controller-20261029:')
    probe=ControllerProbe(template['source_root'],template['model_id'],template['profile_id'],proof['input_sha256'])
    controller=PilotController(probe,observer,output/'operations')
    result={'case_id':'T2-ASYNC','profile_id':proof['profile_id'],'input_sha256':proof['input_sha256'],'state':'RUNNING','points':[]}
    atomic(output/'status.json',result)
    try:
        for replicas in (4,6,8):
            for executors in (4,6,8):
                target=Parameters(replicas,executors,8,8,1000,100)
                snapshot=controller.change(target)
                point='T2-ASYNC-'+str(replicas)+'-'+str(executors);run_id='t2-20261029-r'+str(replicas)+'-e'+str(executors)
                snapshot_file=output/(point+'-before-private.json');save_private(snapshot_file,snapshot)
                cfg={**template,'case_id':'T2-ASYNC','point_id':point,'run_id':run_id,
                    'parameters':target.to_dict(),'snapshot_file':str(snapshot_file),'expected_text_file':config['expected_text_file'],
                    'completed_stages':{'T1-NATIVE':{'state':'COMPLETE','proof_file':str(proof_file),'proof_sha256':config['native_proof_sha256']}}}
                save_private(output/(point+'-config-private.json'),cfg)
                measured=execute(cfg)
                if measured['state']!='COMPLETE':raise RuntimeError('T2 invalid point: '+measured['state'])
                data=read_private(Path(cfg['output_parent'])/run_id/'summary.json')
                controller.check_fixed(probe.capture());controller.quiet()
                row={'point_id':point,'parameters':target.to_dict(),'state':'COMPLETE',
                    'measurement_seconds':data['measurement_seconds'],'measured_successes':data['measured_successes'],
                    'accepted_tasks':len(data['accepted_task_ids']),'throughput_per_second':data['throughput_per_second'],
                    'supply':data['supply'],'generator':data['generator']}
                result['points'].append(row);atomic(output/'status.json',result)
                print(json.dumps(row),flush=True)
        result['state']='RESTORING';atomic(output/'status.json',result)
        restored=controller.restore();result['restored_parameters']=restored['parameters'];result['restored_generation']=restored['generation']
        result['state']='COMPLETE';atomic(output/'status.json',result)
        print(json.dumps({'case_id':'T2-ASYNC','state':'COMPLETE','points':len(result['points'])}),flush=True)
    except BaseException as exc:
        result.update(state='INTERRUPTED_REQUIRES_INSPECTION',error_class=type(exc).__name__);atomic(output/'status.json',result);raise
    finally:observer.close()


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--config',required=True);p.add_argument('--execute',action='store_true');a=p.parse_args()
    config=read_private(a.config)
    if a.execute:run(config)
    else:print(json.dumps({'state':'DRY_RUN','points':9,'changes_configuration':False}))
