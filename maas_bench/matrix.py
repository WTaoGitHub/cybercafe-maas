"""Selectable experiment plan. Adaptive stages never silently become passed."""
import itertools
import json
from pathlib import Path
from .contracts import Parameters

DEFAULT_MATRIX=Path(__file__).resolve().parents[1]/'docs/framework/E3-TUNING-MATRIX.json'


def select(matrix,case_ids=(),modules=(),labels=()):
    stages=matrix['stages']
    for values,key in ((case_ids,'case_id'),(modules,'module'),(labels,'labels')):
        known={v for s in stages for v in (s[key] if key=='labels' else [s[key]])}
        if set(values)-known:raise ValueError('Unknown matrix selector')
    return [s for s in stages if (not case_ids or s['case_id'] in case_ids)
        and (not modules or s['module'] in modules)
        and (not labels or set(labels)&set(s['labels']))]


def plan(matrix,case_ids=(),modules=(),labels=(),ledger=None):
    result=[];ledger=ledger or {}
    entrypoints={'T0-TOOLS':'pytest tests/maas','T0-BASELINE':'python -m maas_bench run-async',
        'T1-NATIVE':'python scripts/maas/runtime/native_tuning_matrix.py --execute',
        'T2-ASYNC':'python scripts/maas/runtime/async_tuning_matrix.py --execute',
        'T3-LIMITS':'python scripts/maas/runtime/admission_tuning.py --execute',
        'T3-EXTEND':'python scripts/maas/runtime/extended_tuning.py --execute',
        'T3-MU':'python scripts/maas/runtime/reference_mu.py --execute',
        'T4-QUEUE':'python scripts/maas/runtime/queue_tuning.py --execute',
        'T5-CLAIM':'python scripts/maas/runtime/claim_tuning.py --execute',
        'T6-CONFIRM':'python scripts/maas/runtime/confirmation_tuning.py --execute',
        'T6-SOAK':'python scripts/maas/runtime/soak_tuning.py --execute',
        **{k:'python scripts/maas/runtime/http_ingress_tuning.py --execute' for k in ('T4-HTTP-ACCEPT','T4-HTTP-REJECT','T4-HTTP-RECOVER')}}
    if matrix.get('queue_policy'):entrypoints['T4-QUEUE']='python scripts/maas/runtime/queue_tuning_v2.py --execute'
    for stage in select(matrix,case_ids,modules,labels):
        unresolved=[]
        for dep in stage['depends_on']:
            state=ledger.get(dep,{}).get('state')
            valid=state=='COMPLETE' or (dep=='T3-EXTEND' and state=='SKIPPED_CONDITION_NOT_MET')
            if not valid:unresolved.append(dep)
        points=[]
        if stage['case_id']=='T0-BASELINE':
            points=[{'point_id':'T0-BASELINE-r'+str(i+1),**matrix['baseline']} for i in range(stage['repeats'])]
        elif stage['kind']=='cartesian':
            dims=stage['dimensions'];keys=list(dims)
            for values in itertools.product(*(dims[k] for k in keys)):
                point=dict(zip(keys,values));point['point_id']=stage['case_id']+'-'+ '-'.join(str(v) for v in values)
                if stage['case_id']=='T2-ASYNC':
                    point.update(global_limit=stage['global_limit'],project_limit=stage['project_limit'],
                        global_queue=matrix['baseline']['global_queue'],project_queue=stage['effective_queue'],
                        vram_claim_bytes=stage['vram_claim_bytes'])
                    Parameters(**{k:v for k,v in point.items() if k!='point_id'})
                points.append(point)
        result.append({'case_id':stage['case_id'],'module':stage['module'],'labels':stage['labels'],
            'unresolved_dependencies':unresolved,'planning_state':'BLOCKED' if unresolved else 'ADAPTIVE_SELECTION_REQUIRED' if not points and stage['case_id']!='T0-TOOLS' else 'PLANNED',
            'points':points,'execution_implemented':stage['case_id'] in entrypoints,
            'execution_entrypoint':entrypoints.get(stage['case_id'])})
    return {'profile_id':matrix['profile_id'],'mode':'PLAN_ONLY_NO_MUTATIONS','stages':result}


def load(path=DEFAULT_MATRIX):return json.loads(Path(path).read_text())


def validate_execution(config,manifest_sha256):
    """Only completed, hashed prerequisite artifacts unlock an async point."""
    import hashlib
    from .experiment import matrix_for,check_proof
    m=matrix_for(config);stage_id=config['case_id']
    adaptive={'T3-LIMITS','T3-EXTEND','T3-MU','T4-QUEUE','T4-HTTP-ACCEPT','T4-HTTP-REJECT','T4-HTTP-RECOVER','T6-CONFIRM','T6-SOAK'}
    if stage_id not in {'T0-BASELINE','T2-ASYNC'}|adaptive:raise ValueError('Unsupported live stage')
    if config['profile_id']!=m['profile_id']:raise ValueError('Unexpected experiment profile')
    ledger=config.get('completed_stages',{})
    proofs={}
    stage=next(s for s in m['stages'] if s['case_id']==stage_id)
    for dependency in stage['depends_on']:
        entry=ledger.get(dependency,{})
        skipped=dependency=='T3-EXTEND' and entry.get('state')=='SKIPPED_CONDITION_NOT_MET'
        if entry.get('state')!='COMPLETE' and not skipped:raise ValueError('Unresolved prerequisite')
        proof_raw=Path(entry['proof_file']).read_bytes()
        if hashlib.sha256(proof_raw).hexdigest()!=entry['proof_sha256']:raise ValueError('Prerequisite evidence changed')
        proof=json.loads(proof_raw)
        proofs[dependency]=proof
        if proof.get('case_id')!=dependency or proof.get('state')!=entry['state']:raise ValueError('Wrong prerequisite evidence')
        if dependency!='T0-TOOLS':check_proof(proof,config)
        if dependency!='T0-TOOLS' and (proof.get('profile_id')!=config['profile_id'] or proof.get('input_sha256')!=manifest_sha256):
            raise ValueError('Prerequisite belongs to another model/input profile')
    if stage_id in adaptive:
        raw=Path(config['selection_file']).read_bytes()
        if hashlib.sha256(raw).hexdigest()!=config['selection_sha256']:raise ValueError('Adaptive selection evidence changed')
        selection=json.loads(raw)
        if selection.get('case_id')!=stage_id or selection.get('profile_id')!=config['profile_id'] or selection.get('input_sha256')!=manifest_sha256:
            raise ValueError('Adaptive selection belongs to another stage/profile')
        check_proof(selection,config)
        if stage_id=='T3-EXTEND' and not selection.get('condition',{}).get('trigger_met'):raise ValueError('Conditional expansion not justified')
        points=selection['points']
    else:points=plan(m,case_ids=[stage_id],ledger=ledger)['stages'][0]['points']
    point=next((p for p in points if p['point_id']==config['point_id']),None)
    if point is None:raise ValueError('Unknown approved point')
    values=point.get('parameters') if stage_id in adaptive else {k:v for k,v in point.items() if k!='point_id'}
    if values!=config['parameters']:
        raise ValueError('Parameters differ from selected matrix point')
    if stage_id in adaptive and point.get('mode','async_saturation')!=config.get('load_mode','async_saturation'):
        raise ValueError('Load mode differs from selected point')
    if stage_id in adaptive and 'arrival_policy' in point and point['arrival_policy']!=config.get('arrival_policy'):
        raise ValueError('Arrival policy differs from selected point')
    if stage_id in adaptive and point.get('workload_sha256')!=config.get('workload_sha256'):
        raise ValueError('Workload differs from selected point')
    if stage_id=='T4-QUEUE' and m.get('queue_policy'):
        from .queue_policy import validate_point
        validate_point(config,selection,point,proofs['T3-MU'])
