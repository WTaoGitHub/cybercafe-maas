import copy
import hashlib
import json
from pathlib import Path
import pytest
from maas_bench import queue_policy as q
from maas_bench.experiment import matrix_for,stamp
from maas_bench.snapshot import save_private,digest
from test_experiment_binding import config as binding_config,ROOT
from test_queue_tuning import load


def setup(tmp_path):
    c=binding_config(tmp_path);source=ROOT/'docs/framework/E3-QUEUE-QOS-V2.json';p=tmp_path/'policy.json';p.write_bytes(source.read_bytes())
    c.update(qos_policy_file=str(p),qos_policy_sha256=hashlib.sha256(p.read_bytes()).hexdigest())
    return c,q.load_policy(c)


def row(policy,scenario='normal',wait=5,accepted=20,rejected=0,qsize=25):
    return {'state':'COMPLETE','point_id':'point','qos_class':scenario,'qos_policy_sha256':policy['_sha256'],
        'parameters':dict(replicas=4,executors=4,global_limit=4,project_limit=4,global_queue=qsize,project_queue=qsize,vram_claim_bytes=2147483648),
        'accepted':accepted,'queue_full_rejections':rejected,'target_arrivals':accepted+rejected,'actual_completed_calls':accepted+rejected,
        'unsent':[],'late_sent_calls':0,'not_scheduled_after_stop':0,'correctness_verified':True,'wait_p95':wait}


@pytest.mark.parametrize('scenario,budget',[('normal',5),('bounded_burst',15),('short_rate_burst',15),('capacity_edge_and_overload',30)])
def test_scenario_boundaries_are_inclusive_and_cannot_be_silently_relaxed(tmp_path,scenario,budget):
    _,p=setup(tmp_path);r=row(p,scenario,budget,accepted=25)
    assert q.assess(r,p)['passed']
    r['wait_p95']=budget+.00001;assert q.assess(r,p)['reasons']==['WAIT_BUDGET']


def test_normal_reject_fails_but_bounded_excess_can_be_rejected(tmp_path):
    _,p=setup(tmp_path)
    assert 'NORMAL_REJECTED' in q.assess(row(p,rejected=1),p)['reasons']
    assert q.assess(row(p,'bounded_burst',10,accepted=25,rejected=175),p)['passed']
    assert 'BURST_ACCEPTANCE_FLOOR' in q.assess(row(p,'bounded_burst',1,accepted=24,rejected=176),p)['reasons']
    assert 'EMPTY_ACCEPTED_SAMPLE' in q.assess(row(p,'bounded_burst',None,accepted=0,rejected=200),p)['reasons']


@pytest.mark.parametrize('change',[{'state':'INTERRUPTED_REQUIRES_INSPECTION'},{'wait_p95':float('nan')},
    {'wait_p95':None},{'actual_completed_calls':19},{'unsent':[1]},{'late_sent_calls':1},
    {'correctness_verified':False},{'qos_policy_sha256':'wrong'}])
def test_invalid_or_unproven_measurement_never_passes(tmp_path,change):
    _,p=setup(tmp_path);r=row(p);r.update(change)
    with pytest.raises(ValueError):q.assess(r,p)


@pytest.mark.parametrize('fault',['changed_file','disabled','threshold','timeout','wrong_profile'])
def test_policy_failure_stops_before_phase_or_mutation(tmp_path,monkeypatch,fault):
    c,_=setup(tmp_path);policy=json.loads(Path(c['qos_policy_file']).read_text())
    if fault=='disabled':policy['execution_enabled']=False
    elif fault=='threshold':policy['classes'][0]['queue_wait_p95_max_seconds']=50
    elif fault=='timeout':policy['common']['task_deadline_seconds']=1200
    elif fault=='wrong_profile':policy['profile_id']='legacy'
    Path(c['qos_policy_file']).write_text(json.dumps(policy))
    if fault!='changed_file':
        c['qos_policy_sha256']=hashlib.sha256(Path(c['qos_policy_file']).read_bytes()).hexdigest()
        m=json.loads(Path(c['matrix_file']).read_text());m['queue_policy']['sha256']=c['qos_policy_sha256']
        Path(c['matrix_file']).write_text(json.dumps(m));c['matrix_sha256']=hashlib.sha256(Path(c['matrix_file']).read_bytes()).hexdigest()
    p=tmp_path/'template.json';save_private(p,c);called=[];module=load('queue_tuning_v2.py')
    monkeypatch.setattr(module,'Phase',lambda *a,**k:called.append(True))
    with pytest.raises(ValueError):module.run({'template_file':str(p)})
    assert not called


def capacity_rows(parameters):
    return [{'run_id':'independent-'+str(i),'point_id':'reference-'+str(i),'state':'COMPLETE','parameters':parameters,
        'measurement_seconds':60,'measured_successes':count,'throughput_per_second':count/60,
        'supply':{'sample_count':100,'ready_queue_min':2,'empty_events':[],'max_observed_sample_gap_seconds':.1}}
        for i,count in enumerate((600,540,660))]


def test_capacity_uses_lowest_repeat_and_rejects_duplicate_starvation_or_other_tuple(tmp_path):
    c,p=setup(tmp_path);params=matrix_for(c)['baseline'];rows=capacity_rows(params)
    assert q.conservative_mu(rows,params)==9
    for key,value in [('run_id','independent-0'),('parameters',{**params,'executors':6}),('state','INCOMPLETE')]:
        bad=copy.deepcopy(rows);bad[1][key]=value
        with pytest.raises(ValueError):q.conservative_mu(bad,params)
    bad=copy.deepcopy(rows);bad[1]['supply']['empty_events']=[{}]
    with pytest.raises(ValueError):q.conservative_mu(bad,params)
    bad=copy.deepcopy(rows);bad[1]['throughput_per_second']=10
    with pytest.raises(ValueError):q.conservative_mu(bad,params)
    points=q.planned({'parameters':params,'conservative_mu':9},p)
    assert len(points)==65 and len({x['point_id'] for x in points})==65
    assert len([x for x in points if x.get('qos_repeat') in (2,3)])==20


def test_completion_clock_does_not_confuse_update_time_with_committed_visibility():
    clock=q.CompletionClock();clock.observe({'db_at':101,'completion_observed_db_at':110,'success_ids':['a']});clock.observe({'db_at':102,'completion_observed_db_at':115,'success_ids':['a','b']})
    times=[{'id':'a','created_epoch':100,'deadline_epoch':112},{'id':'b','created_epoch':100,'deadline_epoch':120}]
    elapsed,_=clock.verify(times);assert elapsed==[10,15]
    times[1]['deadline_epoch']=114
    with pytest.raises(RuntimeError):clock.verify(times)
    with pytest.raises(RuntimeError):clock.verify([{'id':'missing','created_epoch':0,'deadline_epoch':120}])
    with pytest.raises(RuntimeError):clock.observe({'db_at':120,'completion_observed_db_at':119,'success_ids':[]})


def test_legacy_queue_driver_cannot_fallback_for_new_profile(tmp_path):
    c,_=setup(tmp_path);p=tmp_path/'template.json';save_private(p,c)
    with pytest.raises(ValueError,match='legacy fallback'):load('queue_tuning.py').run({'template_file':str(p)})


def test_point_cannot_change_arrivals_or_scenario_after_selection(tmp_path):
    c,p=setup(tmp_path);params=matrix_for(c)['baseline']
    proof={'case_id':'T3-MU','state':'COMPLETE','profile_id':c['profile_id'],'input_sha256':'input',
        'reference_candidates':[{'parameters':params,'conservative_mu':9}],'points':capacity_rows(params),**stamp(c)}
    point=q.planned(proof['reference_candidates'][0],p)[0]
    selection={'input_sha256':'input','reference_mu_sha256':'source','qos_policy_id':q.POLICY_ID,'qos_policy_sha256':c['qos_policy_sha256']}
    c.update(completed_stages={'T3-MU':{'proof_sha256':'source'}},qos_class=point['qos_class'])
    q.validate_point(c,selection,point,proof)
    bad=copy.deepcopy(point);bad['arrival_policy']['clients']=32
    with pytest.raises(ValueError):q.validate_point(c,selection,bad,proof)
    bad=copy.deepcopy(point);bad['qos_class']='capacity_edge_and_overload'
    with pytest.raises(ValueError):q.validate_point(c,selection,bad,proof)


@pytest.mark.parametrize('case',['selected','confirmation_fails','no_candidate','fault'])
def test_sweep_requires_confirmations_and_restores_only_reconciled_decisions(tmp_path,monkeypatch,case):
    from types import SimpleNamespace
    c,p=setup(tmp_path);params=matrix_for(c)['baseline'];candidate={'parameters':params,'conservative_mu':9};points=q.planned(candidate,p)
    fixed={'fixed':'runtime'};mu={'fixed_profile_sha256':digest(fixed)};entry={'proof_sha256':'source'};actions=[];calls=[]
    module=load('queue_tuning_v2.py')
    monkeypatch.setattr(module,'prepare',lambda _:(c,p,mu,entry,candidate,points))
    class Phase:
        def __init__(self,*a,**k):self.status={'state':'RUNNING','points':[]};self.controller=SimpleNamespace(fixed=fixed);self.prerequisites={'T3-MU':entry}
        def point_config(self,point,*args):return point
        def completed(self,value):self.status['points'].append(value)
        def record(self):pass
        def finish(self,**kw):actions.append('finish');self.status.update(state='COMPLETE',**kw)
        def reject(self,*a,**k):actions.append('restore-rejected');self.status.update(state='REJECTED_NO_QUALIFIED_CANDIDATE')
        def interrupted(self,exc):actions.append('interrupt')
        def close(self):actions.append('close')
    monkeypatch.setattr(module,'Phase',Phase)
    def execute(point):
        calls.append(point)
        if case=='fault' and len(calls)==2:raise TimeoutError('uncertain transport')
        count=point['arrival_policy'].get('count',40);size=point['parameters']['project_queue'];n=min(count,size)
        rejected=count-n
        if point['qos_class']=='normal':
            n=count;rejected=0
            if size==10 or case=='no_candidate':n-=1;rejected=1
        value=row(p,point['qos_class'],1,n,rejected,size);value.update(point_id=point['point_id'],parameters=point['parameters'],qos_repeat=point.get('qos_repeat'))
        if case=='confirmation_fails' and size==25 and point.get('qos_repeat')==2:value['wait_p95']=6
        return value
    monkeypatch.setattr(module,'execute_arrivals',execute)
    if case=='fault':
        with pytest.raises(TimeoutError):module.run({'limits_proof_file':'l','extend_proof_file':'e','mu_proof_file':'m'})
        assert actions==['interrupt','close']
    else:
        result=module.run({'limits_proof_file':'l','extend_proof_file':'e','mu_proof_file':'m'})
        if case=='no_candidate':assert actions==['restore-rejected','close'] and len(calls)==45
        else:
            selected=25 if case=='selected' else 50
            assert result['selected_candidate']['parameters']['project_queue']==selected and actions==['finish','close']
            normal=[r for r in result['points'] if r['parameters']['project_queue']==selected and r['qos_class']=='normal']
            assert len(normal)==6 and all(r['qos']['passed'] for r in normal)
            assert len(result['not_run_confirmation_points'])>0


def test_async_screening_emits_two_lower_resource_candidates_in_five_percent_band(tmp_path):
    c,_=setup(tmp_path);base=matrix_for(c)['baseline'];points=[]
    for replicas,rate in ((4,14),(6,15),(8,15.4)):
        points.append({**capacity_rows({**base,'replicas':replicas,'executors':8})[0],
            'throughput_per_second':rate,'measured_successes':int(rate*60)})
    module=load('async_tuning_matrix.py');result=module.screened_candidates(points)
    assert [p['parameters']['replicas'] for p in result]==[6,8]
    points[1]['supply']['empty_events']=[{}]
    with pytest.raises(ValueError):module.screened_candidates(points)
