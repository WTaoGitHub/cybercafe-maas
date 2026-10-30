"""Import an existing verified baseline into a hashed experiment; no inference."""
import argparse
import hashlib
import json
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[3]))
from maas_bench.experiment import matrix_for,stamp
from maas_bench.snapshot import read_private,save_private


def bind(config):
    matrix=matrix_for(config)
    if not stamp(config):raise ValueError('Explicit matrix binding required')
    raw=Path(config['verification_file']).read_bytes()
    if hashlib.sha256(raw).hexdigest()!=config['verification_sha256']:raise ValueError('Baseline verification changed')
    value=json.loads(raw)
    if value.get('state')!='COMPLETE' or value.get('profile_id')!=matrix['profile_id']:
        raise ValueError('Complete matching baseline verification required')
    if value.get('framework_image_id')!=matrix['runtime_binding']['application_image_id']:
        raise ValueError('Baseline framework image mismatch')
    required=('all_accepted_succeeded_attempt_and_metering_one','all_results_downloaded_and_reference_text_verified',
              'runtime_parameters_unchanged','actual_application_source_hashes_verified')
    if any(value.get(k) is not True for k in required) or value.get('unknown')!=0 or value.get('paused') is not False:
        raise ValueError('Incomplete correctness or runtime verification')
    if value.get('parameters')!=matrix['baseline']:raise ValueError('Baseline tuple mismatch')
    rounds=value.get('rounds',[])
    if len(rounds)!=3 or len({r.get('point_id') for r in rounds})!=3:raise ValueError('Three distinct baseline rounds required')
    for r in rounds:
        supply=r.get('supply',{})
        if (r.get('state')!='COMPLETE' or r.get('measurement_seconds',0)<60 or r.get('measured_successes',0)<200
            or supply.get('sample_count',0)<=0 or supply.get('ready_queue_min',0)<=0 or supply.get('empty_events')!=[]
            or not 0<supply.get('max_observed_sample_gap_seconds',0)<=.25
            or r.get('accepted_tasks',0)<=0 or r.get('result_verification',{}).get('downloaded_results')!=r['accepted_tasks']):
            raise ValueError('Invalid complete baseline round')
    if sum(r['accepted_tasks'] for r in rounds)!=value['accepted_tasks']:raise ValueError('Baseline count mismatch')
    return {'case_id':'T0-BASELINE','state':'COMPLETE','profile_id':value['profile_id'],'input_sha256':value['input_sha256'],
        'parameters':value['parameters'],'rounds':rounds,'source_verification_sha256':config['verification_sha256'],
        'source_kind':'verified_baseline_import_no_new_measurement','generation':value['generation'],**stamp(config)}


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--config',required=True);p.add_argument('--write-proof');a=p.parse_args()
    result=bind(read_private(a.config))
    if a.write_proof:save_private(a.write_proof,result)
    print(json.dumps({'state':'PROOF_WRITTEN' if a.write_proof else 'VALIDATED_NO_WRITE','executes_inference':False}))
