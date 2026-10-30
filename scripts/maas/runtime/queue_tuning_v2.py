"""Scenario-specific queue sweep and independent normal confirmations. Explicit only."""
import argparse
import hashlib
import json
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[3]))
from maas_bench.phase import Phase,proof_entry
from maas_bench.snapshot import read_private,digest
from maas_bench.queue_policy import load_policy,reference,planned,assess
from maas_bench.arrival_point import execute_arrivals


def prepare(config):
    template=read_private(config['template_file']);policy=load_policy(template)
    path=Path(config['mu_proof_file'])
    if hashlib.sha256(path.read_bytes()).hexdigest()!=config['mu_proof_sha256']:raise ValueError('Capacity proof changed')
    proof,entry=proof_entry(path,'T3-MU',template)
    if entry['proof_sha256']!=config['mu_proof_sha256']:raise ValueError('Capacity proof changed during read')
    candidate=reference(template,proof)
    return template,policy,proof,entry,candidate,planned(candidate,policy)


def run(config):
    template,policy,mu,entry,candidate,points=prepare(config)
    metadata={'qos_policy_id':policy['policy_id'],'qos_policy_sha256':template['qos_policy_sha256'],'reference_mu_sha256':entry['proof_sha256']}
    phase=Phase(config,'T4-QUEUE',points,[('T3-LIMITS',config['limits_proof_file'],('COMPLETE',)),
        ('T3-EXTEND',config['extend_proof_file'],('COMPLETE','SKIPPED_CONDITION_NOT_MET')),('T3-MU',config['mu_proof_file'],('COMPLETE',))],selection_metadata=metadata)
    def measure(p,n):
        cfg=phase.point_config(p,'t4-v2-'+str(n).zfill(3)+'-arrival')
        row=execute_arrivals(cfg)
        row['qos']=assess(row,policy)
        phase.completed({k:v for k,v in row.items() if k not in ('accepted_task_ids','download_verification')})
        return row
    try:
        if phase.prerequisites['T3-MU']['proof_sha256']!=entry['proof_sha256']:raise ValueError('Capacity proof changed during phase preparation')
        if digest(phase.controller.fixed)!=mu['fixed_profile_sha256']:raise ValueError('Fixed runtime differs from capacity reference')
        phase.status.update(**metadata,reference_mu=candidate['conservative_mu']);phase.record()
        for n,p in enumerate(points[:45]):measure(p,n)
        selected=None
        for q in policy['queue_candidates']:
            slots=min(candidate['parameters'][k] for k in ('executors','global_limit','project_limit'))
            base=[r for r in phase.status['points'] if r['parameters']['project_queue']==q]
            if q<=2*slots or len(base)!=9 or not all(r['qos']['passed'] for r in base):continue
            passed=True
            for n,p in enumerate(points[45:],45):
                if p['parameters']['project_queue']!=q:continue
                row=measure(p,n)
                if not row['qos']['passed']:passed=False;break
            if passed:selected=q;break
        executed={r['point_id'] for r in phase.status['points']}
        skipped=[{'point_id':p['point_id'],'reason':'BASE_SWEEP_OR_CONFIRMATION_REJECTED_OR_LOWER_QUEUE_SELECTED'}
            for p in points[45:] if p['point_id'] not in executed]
        if selected is None:
            phase.reject('No queue satisfies all scenario budgets and confirmation rules',not_run_confirmation_points=skipped)
        else:
            phase.finish(selection_passed=True,selected_candidate={'parameters':{**candidate['parameters'],'global_queue':selected,'project_queue':selected}},
                not_run_confirmation_points=skipped,qos_policy_id=policy['policy_id'],qos_policy_sha256=template['qos_policy_sha256'],
                reference_mu=candidate['conservative_mu'],capacity_ranked=False)
        return phase.status
    except BaseException as exc:phase.interrupted(exc);raise
    finally:phase.close()


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--config',required=True);p.add_argument('--execute',action='store_true');a=p.parse_args()
    if not a.execute:print(json.dumps({'state':'DRY_RUN','configuration_validated':False,'execution':False}))
    else:
        try:
            result=run(read_private(a.config))
            print(json.dumps({'state':result['state'],'automatic_replay':False}))
            if result['state']!='COMPLETE':raise SystemExit(2)
        except Exception as exc:print(json.dumps({'state':'STOPPED','error_class':type(exc).__name__}));raise SystemExit(2)
