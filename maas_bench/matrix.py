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
        'T2-ASYNC':'python scripts/maas/runtime/async_tuning_matrix.py --execute'}
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
    m=load();stage_id=config['case_id']
    if stage_id not in ('T0-BASELINE','T2-ASYNC'):raise ValueError('Live driver supports unchanged pure-async points only')
    if config['profile_id']!=m['profile_id']:raise ValueError('Unexpected experiment profile')
    ledger=config.get('completed_stages',{})
    stage=next(s for s in m['stages'] if s['case_id']==stage_id)
    for dependency in stage['depends_on']:
        entry=ledger.get(dependency,{})
        if entry.get('state')!='COMPLETE':raise ValueError('Unresolved prerequisite')
        proof_raw=Path(entry['proof_file']).read_bytes()
        if hashlib.sha256(proof_raw).hexdigest()!=entry['proof_sha256']:raise ValueError('Prerequisite evidence changed')
        proof=json.loads(proof_raw)
        if proof.get('case_id')!=dependency or proof.get('state')!='COMPLETE':raise ValueError('Wrong prerequisite evidence')
        if dependency!='T0-TOOLS' and (proof.get('profile_id')!=config['profile_id'] or proof.get('input_sha256')!=manifest_sha256):
            raise ValueError('Prerequisite belongs to another model/input profile')
    points=plan(m,case_ids=[stage_id],ledger=ledger)['stages'][0]['points']
    point=next((p for p in points if p['point_id']==config['point_id']),None)
    if point is None:raise ValueError('Unknown approved point')
    if {k:v for k,v in point.items() if k!='point_id'}!=config['parameters']:
        raise ValueError('Parameters differ from selected matrix point')
