"""Versioned queue decisions. A valid measurement may fail QoS without being a fault."""
import hashlib
import json
import math
from pathlib import Path

from .contracts import Parameters
from .experiment import matrix_for,check_proof
from .snapshot import digest

POLICY_ID='E3-T4-QOS-V2'


def load_policy(config):
    raw=Path(config['qos_policy_file']).read_bytes();sha=hashlib.sha256(raw).hexdigest()
    if sha!=config['qos_policy_sha256']:raise ValueError('Queue policy changed')
    p=json.loads(raw);m=matrix_for(config)
    if p.get('policy_id')!=POLICY_ID or p.get('profile_id')!=config['profile_id']:
        raise ValueError('Queue policy version/profile mismatch')
    if m.get('queue_policy')!={'policy_id':POLICY_ID,'sha256':sha}:raise ValueError('Policy not bound by experiment matrix')
    if p.get('execution_enabled') is not True or p.get('driver_v2_implemented') is not True:
        raise ValueError('Queue policy execution disabled')
    expected=[('normal',5,False),('bounded_burst',15,True),('short_rate_burst',15,True),('capacity_edge_and_overload',30,True)]
    classes=p.get('classes',[])
    if [(c.get('id'),c.get('queue_wait_p95_max_seconds'),c.get('queue_full_rejections_allowed')) for c in classes]!=expected:
        raise ValueError('Unsupported v2 scenario thresholds')
    if (p['queue_candidates']!=[10,25,50,100,200] or classes[0]['arrival_rate_multipliers']!=[.5,.8]
        or classes[0]['measurement_seconds']!=180 or classes[0]['selected_candidate_independent_repeats']!=3
        or classes[1]['counts']!=[25,50,100,200] or classes[1]['http_clients']!=16
        or classes[2]['arrival_rate_multiplier']!=2 or classes[2]['measurement_seconds']!=10
        or classes[3]['arrival_rate_multipliers']!=[1.,1.2] or classes[3]['measurement_seconds']!=180):
        raise ValueError('Unsupported v2 arrival plan')
    common=p['common'];mu=p['reference_mu']
    if any(common.get(k)!=v for k,v in {'accepted_success_fraction':1,'task_max_attempts':1,'task_deadline_seconds':120,
        'drain_deadline_seconds':150,'http_client_budget':16,'automatic_request_retry':False,'timeout_extension_allowed':False,
        'unexpected_5xx_allowed':False,'unknown_allowed':False,'lost_task_allowed':False,'duplicate_usage_allowed':False,
        'rejected_fresh_requests_create_task':False,'model_runtime_budget_changed':False}.items()):
        raise ValueError('Unsupported v2 correctness contract')
    if mu.get('window_min_seconds')!=60 or mu.get('window_min_successes')!=200 or mu.get('old_profile_reference_allowed') is not False:
        raise ValueError('Unsupported capacity reference contract')
    if p['measurement'].get('completion_observation_sample_seconds')!=.5:raise ValueError('Unsupported completion observation interval')
    p['_sha256']=sha
    return p


def core(parameters):
    Parameters(**parameters)
    return {k:parameters[k] for k in ('replicas','executors','global_limit','project_limit','vram_claim_bytes')}


def conservative_mu(rows,parameters):
    if len(rows)!=3 or len({r['run_id'] for r in rows})!=3 or len({r['point_id'] for r in rows})!=3:
        raise ValueError('Three independent complete capacity windows required')
    values=[]
    for r in rows:
        if r['state']!='COMPLETE' or r['parameters']!=parameters or r['measurement_seconds']<60 or r['measured_successes']<200:
            raise ValueError('Incomplete or different capacity tuple')
        value=r['throughput_per_second'];supply=r.get('supply',{})
        if (type(value) not in (int,float) or not math.isfinite(value) or value<=0
            or not math.isclose(value,r['measured_successes']/r['measurement_seconds'],rel_tol=1e-9)
            or supply.get('sample_count',0)<=0 or supply.get('ready_queue_min',0)<=0 or supply.get('empty_events')!=[]
            or not 0<supply.get('max_observed_sample_gap_seconds',0)<=.25):
            raise ValueError('Invalid capacity or supply evidence')
        values.append(value)
    return min(values)


def reference(config,proof):
    check_proof(proof,config)
    if proof['case_id']!='T3-MU' or proof['state']!='COMPLETE' or proof['profile_id']!=config['profile_id']:
        raise ValueError('New profile MU proof required')
    candidates=proof['reference_candidates']
    if not 1<=len(candidates)<=2:raise ValueError('One or two capacity references required')
    for c in candidates:
        rows=[r for r in proof['points'] if core(r['parameters'])==core(c['parameters'])]
        if c['conservative_mu']!=conservative_mu(rows,c['parameters']):raise ValueError('Capacity reference is not minimum of complete repeats')
    return min(candidates,key=lambda c:(c['parameters']['replicas'],c['parameters']['executors']))


def planned(candidate,policy):
    mu=candidate['conservative_mu'];points=[];base=candidate['parameters']
    for q in policy['queue_candidates']:
        params={**base,'global_queue':q,'project_queue':q}
        for count in (25,50,100,200):
            points.append({'point_id':f'T4V2-Q{q}-burst{count}','parameters':params,'mode':'bounded_burst','qos_class':'bounded_burst',
                'arrival_policy':{'count':count,'clients':16}})
        for fraction in (.5,.8,1.,1.2):
            points.append({'point_id':f'T4V2-Q{q}-rate{fraction}','parameters':params,'mode':'independent_arrivals',
                'qos_class':'normal' if fraction<1 else 'capacity_edge_and_overload','qos_repeat':1,
                'arrival_policy':{'rate':mu*fraction,'seconds':180,'clients':16}})
        points.append({'point_id':f'T4V2-Q{q}-burst-rate2','parameters':params,'mode':'independent_arrivals','qos_class':'short_rate_burst',
            'arrival_policy':{'rate':mu*2,'seconds':10,'clients':16}})
    # All potential confirmations are selected and hashed before any runtime change.
    for q in policy['queue_candidates']:
        for repeat in (2,3):
            for fraction in (.5,.8):
                points.append({'point_id':f'T4V2-Q{q}-rate{fraction}-repeat{repeat}',
                    'parameters':{**base,'global_queue':q,'project_queue':q},'mode':'independent_arrivals','qos_class':'normal','qos_repeat':repeat,
                    'arrival_policy':{'rate':mu*fraction,'seconds':180,'clients':16}})
    return points


def validate_point(config,selection,point,proof):
    p=load_policy(config);c=reference(config,proof)
    if proof['input_sha256']!=selection['input_sha256']:raise ValueError('Capacity reference inputs differ')
    if selection.get('reference_mu_sha256')!=config['completed_stages']['T3-MU']['proof_sha256']:
        raise ValueError('Selection capacity source differs')
    expected=next((v for v in planned(c,p) if v['point_id']==point['point_id']),None)
    if point!=expected or config.get('qos_class')!=point['qos_class'] or config.get('qos_repeat')!=point.get('qos_repeat'):
        raise ValueError('Arrival scenario differs from policy/capacity reference')
    if selection.get('qos_policy_sha256')!=config['qos_policy_sha256'] or selection.get('qos_policy_id')!=POLICY_ID:
        raise ValueError('Selection policy changed')


def assess(row,policy):
    if row.get('state')!='COMPLETE':raise ValueError('Invalid measurement cannot be selected')
    if row.get('qos_policy_sha256')!=policy['_sha256']:raise ValueError('Point policy provenance differs')
    scenario=next((p for p in policy['classes'] if p['id']==row.get('qos_class')),None)
    if scenario is None:raise ValueError('Unknown arrival scenario')
    n=row['accepted'];rejected=row['queue_full_rejections'];total=row['target_arrivals']
    if (type(n) is not int or type(rejected) is not int or n<0 or rejected<0 or n+rejected!=total
        or row['actual_completed_calls']!=total or row['unsent'] or row['late_sent_calls'] or row['not_scheduled_after_stop']):
        raise ValueError('Incomplete or inconsistent request accounting')
    if row.get('correctness_verified') is not True:raise ValueError('Missing completion/deadline/result verification')
    wait=row.get('wait_p95');reasons=[]
    if n<=0:reasons.append('EMPTY_ACCEPTED_SAMPLE')
    elif type(wait) not in (float,int) or not math.isfinite(wait) or wait<0:raise ValueError('Invalid queue latency')
    elif wait>scenario['queue_wait_p95_max_seconds']:reasons.append('WAIT_BUDGET')
    if not scenario['queue_full_rejections_allowed'] and rejected:reasons.append('NORMAL_REJECTED')
    if row['qos_class']=='bounded_burst' and n<min(total,row['parameters']['global_queue'],row['parameters']['project_queue']):
        reasons.append('BURST_ACCEPTANCE_FLOOR')
    return {'passed':not reasons,'reasons':reasons,'waiting_budget_seconds':scenario['queue_wait_p95_max_seconds']}


class CompletionClock:
    """First committed success observation, an upper bound rather than commit time."""
    def __init__(self):self.seen={}

    def observe(self,row):
        at=row['completion_observed_db_at']
        if type(at) not in (int,float) or not math.isfinite(at):raise ValueError('Invalid observation clock')
        if at<row['db_at']:raise RuntimeError('Database observation clock moved backwards')
        new=[]
        for ident in row['success_ids']:
            if ident not in self.seen:self.seen[ident]=at;new.append(ident)
        return new

    def verify(self,timings):
        elapsed=[];observations=[]
        for row in timings:
            at=self.seen.get(row['id'])
            if at is None or at>row['deadline_epoch']:raise RuntimeError('Completion timing not proven within task deadline')
            if at<row['created_epoch']:raise RuntimeError('Invalid database observation interval')
            elapsed.append(at-row['created_epoch']);observations.append({'task_id':row['id'],'observed_db_epoch':at})
        return elapsed,observations
