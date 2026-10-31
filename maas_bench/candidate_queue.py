"""Explicit Q25 confirmation, not a queue search or legacy-proof substitution."""
import hashlib
import json
from pathlib import Path
from .snapshot import read_private,digest
from .candidate_qualification import reference_rates,planned as qualification_plan
from .claim_transition import model_without_claim
from .queue_policy import load_policy

PROTOCOL='C35-Q25-CONFIRM-V1'
CASE='C35-Q25-CONFIRM'


def qualification(config):
    path=Path(config['candidate_mu_file'])
    if hashlib.sha256(path.read_bytes()).hexdigest()!=config['candidate_mu_sha256']:raise ValueError('Candidate qualification changed')
    proof=read_private(path)
    if proof.get('case_id')!='CANDIDATE-QUALIFICATION-35' or proof.get('state')!='COMPLETE':raise ValueError('Complete new-claim qualification required')
    params=proof['parameters']
    if params!={'replicas':8,'executors':8,'global_limit':8,'project_limit':8,'global_queue':25,'project_queue':25,'vram_claim_bytes':int(3.5*1024**3)}:
        raise ValueError('Only the qualified 8/8 Q25 3.5GiB tuple is supported')
    plan=qualification_plan()
    if len(proof['points'])!=len(plan):raise ValueError('Incomplete calibration')
    for i,(p,expected) in enumerate(zip(proof['points'],plan)):
        if p['parameters']!=params or any(p[k]!=v for k,v in expected.items()):raise ValueError('Qualification plan differs')
        f=Path(config['candidate_mu_points_root'])/('point-'+str(i))/'summary-private.json'
        if hashlib.sha256(f.read_bytes()).hexdigest()!=p['source_summary_sha256']:raise ValueError('Calibration source summary changed')
        raw=read_private(f)
        for k in ('state','parameters','measurement_seconds','measured_successes','throughput_per_second','supply'):
            if raw[k]!=p[k]:raise ValueError('Calibration does not match source summary')
    q=proof['points'][0]
    if not q['resource_qualified'] or q['measurement_seconds']<300 or q['idle_seconds']!=60 or len(q['card_memory'])!=2 or not all(c['plateau_observed'] and c['minimum_logged_free_bytes']>=max(1024**3,.1*c['total_bytes']) for c in q['card_memory']):
        raise ValueError('Missing new-claim resource gate')
    if reference_rates(proof['points'],params)!=proof['reference_mu']:raise ValueError('Incorrect workload capacity reference')
    if proof['profile_id']!=config['profile_id'] or proof['input_sha256']!=digest(read_private(config['fixture_file'])):
        raise ValueError('Qualification belongs to different inputs/profile')
    return proof


def planned(proof):
    points=[];params=proof['parameters']
    for kind in ('ordinary','mixed'):
        mu=proof['reference_mu'][kind];common={'workload':kind,'parameters':params}
        for repeat in (1,2,3):
            for fraction in (.5,.8):
                points.append({**common,'point_id':kind+'-normal-'+str(fraction)+'-r'+str(repeat),'mode':'independent_arrivals','qos_class':'normal','qos_repeat':repeat,
                    'arrival_policy':{'seconds':180,'rate':mu*fraction,'clients':16}})
        for count in (25,50,100,200):
            points.append({**common,'point_id':kind+'-burst-'+str(count),'mode':'bounded_burst','qos_class':'bounded_burst',
                'arrival_policy':{'count':count,'clients':16}})
        points.append({**common,'point_id':kind+'-short-2mu','mode':'independent_arrivals','qos_class':'short_rate_burst',
            'arrival_policy':{'seconds':10,'rate':mu*2,'clients':16}})
        for fraction in (1.,1.2):
            points.append({**common,'point_id':kind+'-edge-'+str(fraction),'mode':'independent_arrivals','qos_class':'capacity_edge_and_overload',
                'arrival_policy':{'seconds':180,'rate':mu*fraction,'clients':16}})
        points.append({**common,'point_id':kind+'-recovery-3','mode':'bounded_burst','qos_class':'normal','phase_kind':'recovery',
            'arrival_policy':{'count':3,'clients':1}})
    return points


def compatible_runtime(source,current):
    for key in ('profile_id','input_sha256','workers','catalog_sha256','app_fixed_env_sha256','gpustack_version'):
        if source[key]!=current[key]:raise ValueError('Candidate capacity runtime drift')
    if model_without_claim(source['model'])!=model_without_claim(current['model']):raise ValueError('Candidate model drift')
    if {(a['service'],a['image']) for a in source['app_containers']}!={(a['service'],a['image']) for a in current['app_containers']}:
        raise ValueError('Candidate application image drift')


def validate_execution(config,ordinary):
    if config.get('candidate_queue_protocol')!=PROTOCOL or config.get('case_id')!=CASE:raise ValueError('Unsupported candidate queue protocol')
    proof=qualification(config)
    runtime=Path(config['candidate_runtime_file'])
    if hashlib.sha256(runtime.read_bytes()).hexdigest()!=config['candidate_runtime_sha256']:raise ValueError('Candidate runtime evidence changed')
    source=read_private(runtime);current=read_private(config['snapshot_file'])
    if source['parameters']!=proof['parameters'] or current['parameters']!=proof['parameters']:raise ValueError('Wrong actual candidate tuple')
    compatible_runtime(source,current)
    path=Path(config['selection_file'])
    if hashlib.sha256(path.read_bytes()).hexdigest()!=config['selection_sha256']:raise ValueError('Candidate selection changed')
    selection=read_private(path);expected=planned(proof)
    if selection.get('protocol')!=PROTOCOL or selection['points']!=expected or selection['candidate_mu_sha256']!=config['candidate_mu_sha256']:raise ValueError('Candidate selection is not frozen plan')
    matches=[p for p in expected if p['point_id']==config['point_id']]
    if len(matches)!=1:raise ValueError('Unknown candidate confirmation point')
    p=matches[0]
    for key in ('parameters','arrival_policy','qos_class','qos_repeat','workload','phase_kind'):
        if config.get(key)!=p.get(key):raise ValueError('Candidate point differs from selected plan')
    if config['load_mode']!=p['mode'] or config['workload_sha256']!=selection['workload_sha256'][p['workload']]:raise ValueError('Candidate workload/mode differs')
    policy=load_policy(config)
    if selection['qos_policy_sha256']!=policy['_sha256']:raise ValueError('Candidate policy differs')
    return policy
