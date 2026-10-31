"""New-claim T6 protocol; never impersonates or relaxes the legacy matrix."""
import hashlib
from pathlib import Path
from .snapshot import read_private,digest
from .candidate_queue import qualification,planned as queue_plan,compatible_runtime,PROTOCOL as QUEUE_PROTOCOL,CASE as QUEUE_CASE
from .queue_policy import load_policy,assess,conservative_mu

PROTOCOL='C35-T6-V1'
CASE='T6-C35'


def bound(config,key):
    p=Path(config[key])
    sha_key='candidate_runtime_sha256' if key=='candidate_runtime_file' else key+'_sha256'
    if hashlib.sha256(p.read_bytes()).hexdigest()!=config[sha_key]:raise ValueError('T6 bound evidence changed: '+key)
    return read_private(p)


def prerequisites(config):
    q=qualification(config);s=bound(config,'queue_proof_file');policy=load_policy(config)
    if s.get('state')!='COMPLETE' or s.get('case_id')!=QUEUE_CASE or s.get('protocol')!=QUEUE_PROTOCOL or not s.get('queue_qualified') or not s.get('http_overload_recovery_qualified'):
        raise ValueError('New claim Q25 and contiguous HTTP recovery required')
    if s['parameters']!=q['parameters'] or s['planned_points']!=queue_plan(q) or len(s['points'])!=28:raise ValueError('Incomplete matching Q25 evidence')
    root=Path(config['queue_proof_file']).parent
    for i,p in enumerate(s['points']):
        c=read_private(root/('point-'+str(i))/'config-private.json');f=root/('point-'+str(i))/c['run_id']/'summary.json'
        if hashlib.sha256(f.read_bytes()).hexdigest()!=p['source_summary_sha256']:raise ValueError('Q25 source summary changed')
        raw=read_private(f)
        if raw['parameters']!=q['parameters'] or raw['candidate_mu_sha256']!=config['candidate_mu_sha256'] or raw['point_id']!=s['planned_points'][i]['point_id'] or assess(raw,policy)!=p['qos'] or not p['qos']['passed']:
            raise ValueError('Q25 source does not prove this candidate')
        if s['planned_points'][i].get('phase_kind')=='recovery' and (raw['accepted']!=3 or raw['queue_full_rejections'] or s['points'][i-1]['queue_full_rejections']<=0 or p['configuration_changed_between_overload_and_probe'] is not False):
            raise ValueError('Contiguous overload recovery missing')
    return q


def capacity_plan(original,target):
    points=[]
    for repeat in (1,2,3):
        order=('baseline','candidate') if repeat%2 else ('candidate','baseline')
        for role in order:points.append({'point_id':f'capacity-ordinary-{role}-{repeat}','workload':'ordinary','role':role,'repeat':repeat,
            'parameters':original if role=='baseline' else target,'mode':'async_saturation','seconds':300})
    points.append({'point_id':'capacity-mixed-resource','workload':'mixed','role':'resource','parameters':target,'mode':'async_saturation','seconds':300,'idle_seconds':60})
    for repeat in (1,2,3):points.append({'point_id':f'capacity-mixed-candidate-{repeat}','workload':'mixed','role':'candidate','repeat':repeat,'parameters':target,'mode':'async_saturation','seconds':300})
    return points


def capacity_rates(proof):
    params=proof['parameters'];rows=proof['points']
    return {kind:conservative_mu([r for r in rows if r['role']=='candidate' and r['workload']==kind],params) for kind in ('ordinary','mixed')}


def latency_plan(params,rates):
    return [{'point_id':f'latency-{kind}-{fraction}','parameters':params,'workload':kind,'mode':'independent_arrivals','qos_class':'normal',
        'arrival_policy':{'rate':rates[kind]*fraction,'seconds':180,'clients':16},'fraction':fraction} for kind in ('ordinary','mixed') for fraction in (.5,.8)]


def soak_plan(params,rates):
    common={'parameters':params,'workload':'mixed'}
    points=[{**common,'point_id':'soak-resource','role':'resource','mode':'async_saturation','seconds':300,'idle_seconds':60},
            {**common,'point_id':'soak-probe-before','mode':'bounded_burst','qos_class':'normal','arrival_policy':{'count':1,'clients':1}}]
    for n in range(24):
        mode=('async_saturation','independent_arrivals','mixed_20','independent_arrivals')[n%4]
        p={**common,'point_id':f'soak-segment-{n:02d}','mode':mode,'segment':n}
        if mode=='independent_arrivals':p.update(qos_class='normal',arrival_policy={'rate':rates['mixed']*.5,'seconds':300,'clients':16})
        else:p['seconds']=300
        if mode=='mixed_20':p['sync_rate']=rates['mixed']*.2
        points.append(p)
        if n%4==3:points.append({**common,'point_id':f'soak-burst-{n}','mode':'bounded_burst','qos_class':'bounded_burst','arrival_policy':{'count':min(200,4*params['project_queue']),'clients':16}})
    points.append({**common,'point_id':'soak-probe-after','mode':'bounded_burst','qos_class':'normal','arrival_policy':{'count':1,'clients':1}})
    return points


def verified_capacity(config,original,target):
    s=bound(config,'capacity_proof_file')
    if s.get('state')!='COMPLETE' or s.get('case_id')!='T6-C35-CAPACITY' or s['parameters']!=target or s['planned_points']!=capacity_plan(original,target) or s.get('queue_proof_sha256')!=config['queue_proof_file_sha256']:raise ValueError('Matching formal capacity proof required')
    root=Path(config['capacity_proof_file']).parent
    if len(s['points'])!=10:raise ValueError('Incomplete capacity proof')
    for i,(r,p) in enumerate(zip(s['points'],s['planned_points'])):
        f=root/('point-'+str(i))/'result-private.json'
        if hashlib.sha256(f.read_bytes()).hexdigest()!=r['source_result_sha256']:raise ValueError('Formal capacity source changed')
        raw=read_private(f)
        if raw!={k:v for k,v in r.items() if k!='source_result_sha256'}:raise ValueError('Capacity proof differs from source result')
        if any(raw.get(k)!=v for k,v in p.items()) or raw['state']!='COMPLETE' or raw['measurement_seconds']<300:raise ValueError('Invalid formal capacity window')
        summary=root/('point-'+str(i))/raw['run_id']/'summary.json'
        if hashlib.sha256(summary.read_bytes()).hexdigest()!=raw['source_summary_sha256']:raise ValueError('Capacity measurement summary changed')
        measured=read_private(summary)
        if any(measured[k]!=raw[k] for k in ('state','measurement_seconds','measured_successes','throughput_per_second','supply','completion_witness')) or measured['parameters']!=p['parameters'] or not measured['completion_witness']['all_accepted_observed_before_deadline']:
            raise ValueError('Formal measurement differs from result')
        if measured.get('candidate_t6_protocol')!=PROTOCOL or measured.get('queue_proof_sha256')!=config['queue_proof_file_sha256']:raise ValueError('Formal measurement lineage differs')
        if p['role']=='resource' and not raw.get('resource_qualified'):raise ValueError('Mixed resource gate missing')
    if s['reference_mu']!=capacity_rates(s):raise ValueError('Incorrect formal reference rates')
    return s


def verified_confirmation(config,capacity):
    s=bound(config,'confirmation_proof_file');expected=latency_plan(capacity['parameters'],capacity['reference_mu'])
    if s.get('state')!='COMPLETE' or s.get('case_id')!='T6-C35-CONFIRM' or s.get('parameters')!=capacity['parameters'] or s.get('capacity_proof_sha256')!=config['capacity_proof_file_sha256'] or s.get('planned_points')!=expected or len(s.get('points',[]))!=4 or not s.get('latency_qualified'):
        raise ValueError('New formal latency confirmation required')
    policy=load_policy(config);root=Path(config['confirmation_proof_file']).parent;eligible=[]
    for i,(r,p) in enumerate(zip(s['points'],expected)):
        f=root/('point-'+str(i))/'result-private.json'
        if hashlib.sha256(f.read_bytes()).hexdigest()!=r['source_result_sha256'] or read_private(f)!={k:v for k,v in r.items() if k!='source_result_sha256'}:raise ValueError('Latency source result changed')
        summary=root/('point-'+str(i))/r['run_id']/'summary.json'
        if hashlib.sha256(summary.read_bytes()).hexdigest()!=r['source_summary_sha256']:raise ValueError('Latency source measurement changed')
        raw=read_private(summary)
        if any(r.get(k)!=v for k,v in p.items()) or any(raw[k]!=r[k] for k in ('parameters','wait_p95','total_p95','total_p99','accepted','queue_full_rejections')) or not assess(raw,policy)['passed'] or not raw.get('correctness_verified') or raw.get('candidate_t6_protocol')!=PROTOCOL:raise ValueError('Latency result does not meet frozen contract')
        if p['workload']=='ordinary' and raw['queue_full_rejections']==0 and raw['total_p95']<=1.5 and raw['total_p99']<=3:eligible.append(raw['target_rate'])
    if not eligible or s.get('validated_ordinary_arrival_rate')!=max(eligible):raise ValueError('Formal ordinary latency eligibility missing')
    return s


def validate_execution(config,ordinary):
    if config.get('candidate_t6_protocol')!=PROTOCOL or config.get('case_id')!=CASE:raise ValueError('Unsupported new-claim T6 protocol')
    q=prerequisites(config);original=bound(config,'original_runtime_file');source=bound(config,'candidate_runtime_file');current=read_private(config['snapshot_file'])
    if q['input_sha256']!=digest(ordinary):raise ValueError('T6 input lineage drift')
    compatible_runtime(source,current);compatible_runtime(original,current)
    selection=bound(config,'selection_file');stage=selection['stage'];target=q['parameters']
    if selection.get('protocol')!=PROTOCOL or selection['queue_proof_sha256']!=config['queue_proof_file_sha256']:raise ValueError('Wrong T6 selection lineage')
    if stage=='capacity':expected=capacity_plan(original['parameters'],target)
    elif stage in ('latency','soak'):
        cap=verified_capacity(config,original['parameters'],target)
        if selection['capacity_proof_sha256']!=config['capacity_proof_file_sha256']:raise ValueError('Selection capacity binding differs')
        expected=latency_plan(target,cap['reference_mu']) if stage=='latency' else soak_plan(target,cap['reference_mu'])
        if stage=='soak':
            verified_confirmation(config,cap)
    else:raise ValueError('Unknown T6 stage')
    if selection['points']!=expected:raise ValueError('T6 plan differs from protocol')
    matches=[p for p in expected if p['point_id']==config['point_id']]
    if len(matches)!=1:raise ValueError('Unknown T6 point')
    p=matches[0]
    for k in ('parameters','workload','arrival_policy','qos_class','sync_rate'):
        if config.get(k)!=p.get(k):raise ValueError('T6 point/config mismatch: '+k)
    if config['parameters']!=current['parameters'] or config['load_mode']!=p['mode'] or config['workload_sha256']!=selection['workload_sha256'][p['workload']]:raise ValueError('T6 tuple/mode/input mismatch')
    if p['mode'] in ('async_saturation','mixed_20') and config['policy']!={'seconds':300,'min_successes':200,'max_seconds':300}:raise ValueError('T6 formal window changed')
    return load_policy(config)
