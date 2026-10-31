import copy
import hashlib
import json
import math
import pytest
from pathlib import Path
from maas_bench.snapshot import read_private,save_private,digest
from maas_bench.candidate_t6 import capacity_plan,latency_plan,soak_plan,prerequisites,validate_execution,PROTOCOL,CASE


def put(path,value):
    save_private(path,value);return str(path),hashlib.sha256(path.read_bytes()).hexdigest()


def files(tmp_path,monkeypatch):
    from test_candidate_queue import fixture_files
    from maas_bench.candidate_queue import planned,PROTOCOL as QPROTO,CASE as QCASE
    c,m=fixture_files(tmp_path,monkeypatch);proof=read_private(c['candidate_mu_file'])
    policy={'_sha256':'policy','classes':[{'id':n,'queue_wait_p95_max_seconds':t,'queue_full_rejections_allowed':allow} for n,t,allow in [('normal',5,False),('bounded_burst',15,True),('short_rate_burst',15,True),('capacity_edge_and_overload',30,True)]]}
    monkeypatch.setattr('maas_bench.candidate_t6.load_policy',lambda _:policy)
    root=tmp_path/'queue';root.mkdir(mode=0o700);points=planned(proof);rows=[]
    for i,p in enumerate(points):
        d=root/('point-'+str(i));d.mkdir(mode=0o700);run='source-point-'+str(i);f=d/run;f.mkdir(mode=0o700);put(d/'config-private.json',{'run_id':run})
        a=p['arrival_policy'];n=a.get('count',math.ceil(a.get('seconds',1)*a.get('rate',1)));rejected=int(p['point_id'].endswith('edge-1.2'))
        raw={'state':'COMPLETE','point_id':p['point_id'],'parameters':proof['parameters'],'candidate_mu_sha256':c['candidate_mu_sha256'],
             'qos_policy_sha256':'policy','qos_class':p['qos_class'],'accepted':n-rejected,'queue_full_rejections':rejected,'target_arrivals':n,
             'actual_completed_calls':n,'unsent':[],'late_sent_calls':0,'not_scheduled_after_stop':0,'correctness_verified':True,'wait_p95':.1}
        _,sha=put(f/'summary.json',raw);rows.append({**raw,'source_summary_sha256':sha,'qos':{'passed':True,'reasons':[],'waiting_budget_seconds':next(x['queue_wait_p95_max_seconds'] for x in policy['classes'] if x['id']==p['qos_class'])},'configuration_changed_between_overload_and_probe':False})
    s={'state':'COMPLETE','case_id':QCASE,'protocol':QPROTO,'queue_qualified':True,'http_overload_recovery_qualified':True,'parameters':proof['parameters'],'planned_points':points,'points':rows}
    c['queue_proof_file'],c['queue_proof_file_sha256']=put(root/'status.json',s)
    original=read_private(c['snapshot_file']);original['input_sha256']=digest(m)
    f=Path(c['candidate_runtime_file']);f.write_text(json.dumps(original));c['candidate_runtime_sha256']=hashlib.sha256(f.read_bytes()).hexdigest()
    original['parameters']={**proof['parameters'],'replicas':4,'executors':4,'global_limit':4,'project_limit':4,'global_queue':1000,'project_queue':100,'vram_claim_bytes':2*1024**3};original['model']['env']['GPUSTACK_MODEL_VRAM_CLAIM']=str(2*1024**3)
    c['original_runtime_file'],c['original_runtime_file_sha256']=put(tmp_path/'original.json',original)
    points=capacity_plan(original['parameters'],proof['parameters']);selection={'protocol':PROTOCOL,'stage':'capacity','queue_proof_sha256':c['queue_proof_file_sha256'],'points':points,'workload_sha256':{'ordinary':'ordinary-hash','mixed':'mixed-hash'}}
    c['selection_file'],c['selection_file_sha256']=put(tmp_path/'t6-selection.json',selection)
    c['snapshot_file']=c['original_runtime_file'];c.update(candidate_t6_protocol=PROTOCOL,case_id=CASE,**points[0],load_mode=points[0]['mode'],policy={'seconds':300,'min_successes':200,'max_seconds':300})
    for k in ('candidate_queue_protocol','arrival_policy','qos_class','qos_repeat'):c.pop(k,None)
    return c,m,policy,proof


def test_fixed_plans_have_nine_capacity_windows_and_real_7200_formal_seconds():
    from test_candidate_queue import proof
    p=proof()['parameters'];base={**p,'replicas':4,'executors':4};rows=capacity_plan(base,p)
    assert len(rows)==10 and sum(r['role']=='baseline' for r in rows)==3 and sum(r['role']=='candidate' for r in rows)==6
    assert [r['role'] for r in rows[:6]]==['baseline','candidate','candidate','baseline','baseline','candidate']
    assert rows[6]['role']=='resource' and rows[6]['seconds']==300 and rows[6]['idle_seconds']==60
    rates={'ordinary':16,'mixed':12};assert sorted(r['arrival_policy']['rate'] for r in latency_plan(p,rates))==pytest.approx([6,8,9.6,12.8])
    soak=soak_plan(p,rates);segments=[r for r in soak if 'segment' in r]
    assert len(segments)==24 and sum(r.get('seconds',r.get('arrival_policy',{}).get('seconds',0)) for r in segments)==7200
    assert sum(r['mode']=='mixed_20' for r in segments)==6 and all(r['sync_rate']==pytest.approx(2.4) for r in segments if r['mode']=='mixed_20')
    assert len(soak)==33 and len({r['point_id'] for r in soak})==33


def test_new_t6_binds_previous_queue_sources_and_exact_baseline(tmp_path,monkeypatch):
    c,m,policy,p=files(tmp_path,monkeypatch);assert prerequisites(c)['parameters']==p['parameters'];assert validate_execution(c,m)==policy
    for k,v in [('candidate_t6_protocol','legacy'),('case_id','T6-SOAK'),('workload','mixed'),('policy',{'seconds':60,'min_successes':200,'max_seconds':300}),('parameters',p['parameters'])]:
        bad=copy.deepcopy(c);bad[k]=v
        with pytest.raises(ValueError):validate_execution(bad,m)


def test_prerequisite_summary_tamper_or_failed_recovery_rejected(tmp_path,monkeypatch):
    c,m,_,_=files(tmp_path,monkeypatch);Path(c['queue_proof_file']).parent.joinpath('point-0','source-point-0','summary.json').write_text('{}')
    with pytest.raises(ValueError,match='source summary'):prerequisites(c)


def test_bound_selection_cannot_add_arbitrary_parameters_or_shorten_soak(tmp_path,monkeypatch):
    c,m,_,_=files(tmp_path,monkeypatch);f=Path(c['selection_file']);s=read_private(f);s['points'][0]['parameters']['executors']=16;f.write_text(json.dumps(s));f.chmod(0o600);c['selection_file_sha256']=hashlib.sha256(f.read_bytes()).hexdigest()
    with pytest.raises(ValueError,match='plan differs'):validate_execution(c,m)
    f.write_text('{}')
    with pytest.raises(ValueError,match='evidence changed'):validate_execution(c,m)


def test_soak_cannot_start_without_matching_formal_capacity_proof(tmp_path,monkeypatch):
    c,m,_,p=files(tmp_path,monkeypatch);s=read_private(c['selection_file']);s['stage']='soak';s['points']=soak_plan(p['parameters'],p['reference_mu']);f=Path(c['selection_file']);f.write_text(json.dumps(s));c['selection_file_sha256']=hashlib.sha256(f.read_bytes()).hexdigest()
    c['capacity_proof_file'],c['capacity_proof_file_sha256']=put(tmp_path/'bad-capacity.json',{'state':'COMPLETE','case_id':'T6-CONFIRM','parameters':p['parameters'],'planned_points':[]})
    with pytest.raises(ValueError,match='formal capacity proof'):validate_execution(c,m)


def test_driver_dry_preconditions_never_connect_without_scope_and_backup():
    from test_queue_tuning import load
    driver=load('run_candidate_t6.py')
    with pytest.raises(ValueError,match='scope'):driver.run({})
    with pytest.raises(ValueError,match='backup'):driver.run({'scope':'C35_FORMAL_CONFIRMATION_AND_FULL_T6'})


def capacity_files(tmp_path,monkeypatch):
    from maas_bench.candidate_t6 import capacity_rates
    c,m,policy,p=files(tmp_path,monkeypatch);original=read_private(c['original_runtime_file'])['parameters'];plans=capacity_plan(original,p['parameters'])
    root=tmp_path/'formal';root.mkdir(mode=0o700);rows=[]
    for i,plan in enumerate(plans):
        d=root/('point-'+str(i));d.mkdir(mode=0o700);run='formal-'+str(i);f=d/run;f.mkdir(mode=0o700);rate=8 if plan['role']=='baseline' else 16 if plan['workload']=='ordinary' else 12
        raw={'state':'COMPLETE','parameters':plan['parameters'],'measurement_seconds':300,'measured_successes':300*rate,'throughput_per_second':rate,'supply':{'sample_count':300,'ready_queue_min':8,'empty_events':[],'max_observed_sample_gap_seconds':.1},'completion_witness':{'all_accepted_observed_before_deadline':True},'candidate_t6_protocol':PROTOCOL,'queue_proof_sha256':c['queue_proof_file_sha256']}
        _,sha=put(f/'summary.json',raw);row={**raw,**plan,'run_id':run,'source_summary_sha256':sha,'resource_qualified':True};_,sha=put(d/'result-private.json',row);rows.append({**row,'source_result_sha256':sha})
    s={'state':'COMPLETE','case_id':'T6-C35-CAPACITY','parameters':p['parameters'],'planned_points':plans,'points':rows,'queue_proof_sha256':c['queue_proof_file_sha256']};s['reference_mu']=capacity_rates(s)
    c['capacity_proof_file'],c['capacity_proof_file_sha256']=put(root/'proof.json',s)
    return c,s


def test_formal_capacity_recomputes_rates_and_binds_results_and_measurements(tmp_path,monkeypatch):
    from maas_bench.candidate_t6 import verified_capacity
    c,s=capacity_files(tmp_path,monkeypatch);base=read_private(c['original_runtime_file'])['parameters'];assert verified_capacity(c,base,s['parameters'])['reference_mu']=={'ordinary':16,'mixed':12}
    f=Path(c['capacity_proof_file']);bad=copy.deepcopy(s);bad['points'][1]['throughput_per_second']=99;f.write_text(json.dumps(bad));c['capacity_proof_file_sha256']=hashlib.sha256(f.read_bytes()).hexdigest()
    with pytest.raises(ValueError,match='differs from source result'):verified_capacity(c,base,s['parameters'])
    f.write_text(json.dumps(s));c['capacity_proof_file_sha256']=hashlib.sha256(f.read_bytes()).hexdigest();f.parent.joinpath('point-1','formal-1','summary.json').write_text('{}')
    with pytest.raises(ValueError,match='summary changed'):verified_capacity(c,base,s['parameters'])


@pytest.mark.parametrize('interrupt',[False,True])
def test_full_driver_orchestration_is_one_shot_and_restores_only_after_success(tmp_path,monkeypatch,interrupt):
    """Synthetic clocks/inference test orchestration, never performance evidence."""
    from types import SimpleNamespace
    from maas_bench.contracts import Parameters
    from test_queue_tuning import load
    m=load('run_candidate_t6.py');target=Parameters(8,8,8,8,25,25,int(3.5*1024**3));base=Parameters(4,4,4,4,1000,100).to_dict()
    original={'parameters':base,'generation':10};state=copy.deepcopy(original);actions=[];root=tmp_path/'run';template=tmp_path/'template.json';save_private(template,{'source_root':str(tmp_path),'model_id':18,'profile_id':'p','database_dsn_file':'dsn','project':'p','logical_model':'ocr','revision':'1'})
    class Probe:
        def __init__(self,*_):pass
        def capture(self):return copy.deepcopy(state)
    class Controller:
        def __init__(self,probe,*_,**kw):self.probe=probe;self.original=copy.deepcopy(original);self.expected=copy.deepcopy(state)
        def quiet(self):pass
        def change(self,p):state.update(parameters=p.to_dict(),generation=state['generation']+2);self.expected=copy.deepcopy(state);actions.append(p.to_dict());return copy.deepcopy(state)
    def change_claim(controller,amount,count,**kwargs):
        state['parameters']={**state['parameters'],'vram_claim_bytes':amount,'replicas':count};state['generation']+=2;controller.expected=copy.deepcopy(state);actions.append({'claim':amount,'restoring':kwargs.get('restoring',False)})
    monkeypatch.setattr(m,'ControllerProbe',Probe);monkeypatch.setattr(m,'PilotController',Controller);monkeypatch.setattr(m,'change_claim',change_claim)
    monkeypatch.setattr(m,'prerequisites',lambda _:{'input_sha256':'i','parameters':target.to_dict()});monkeypatch.setattr(m,'proof_entry',lambda *_:({'input_sha256':'i'},None));monkeypatch.setattr(m,'load_refresh',lambda *_:({},dict(ordinary={'fixtures':[]},mixed={'fixtures':[]})))
    monkeypatch.setattr(m,'compatible_runtime',lambda *_:None);monkeypatch.setattr(m,'check_runtime',lambda *_:None);monkeypatch.setattr(m,'validate_evidence',lambda *_:target)
    monkeypatch.setattr(m,'bound',lambda _,k:original if k in ('original_runtime_file','candidate_runtime_file') else {'state':'READY','original_runtime_sha256':'a'*64,'fixture_refresh_sha256':'b'*64,'forecast_bytes':1000,'soak_forecast_bytes':500} if k=='reservation_file' else {})
    monkeypatch.setattr(m,'read_secret',lambda _:'unused');monkeypatch.setattr(m,'DatabaseObserver',lambda *_a,**_k:SimpleNamespace(close=lambda:None));monkeypatch.setattr(m,'require_inputs',lambda *_:None)
    monkeypatch.setattr(m,'live_storage',lambda _:{'current_bytes':0,'quota_bytes':4*1024**3,'global_current_bytes':0,'global_quota_bytes':10*1024**3,'disk_free_bytes':100*1024**3,'min_free_bytes':2*1024**3})
    monkeypatch.setattr(m,'fresh_namespace',lambda *_:None);monkeypatch.setattr(m,'workload_extra',lambda *_:{'workload_sha256':'workload'});monkeypatch.setattr(m,'load_policy',lambda _:{})
    monkeypatch.setattr(m,'resource_gate',lambda *_:[{'plateau_observed':True}]*2);monkeypatch.setattr(m,'assess',lambda *_:{'passed':True})
    ticks=iter(range(0,1000000,10000));monkeypatch.setattr(m,'time',SimpleNamespace(time=__import__('time').time,monotonic=lambda:next(ticks)))
    def execute(c):
        if interrupt and c['point_id']=='capacity-ordinary-candidate-1':raise RuntimeError('synthetic failure')
        rate=8 if c['parameters']==base else 16 if c['workload']=='ordinary' else 12;n=300*rate
        p=Path(c['output_parent'])/c['run_id'];p.mkdir(mode=0o700)
        raw={'state':'COMPLETE','measurement_seconds':300.01,'measured_successes':n,'throughput_per_second':n/300.01,'supply':{'sample_count':300,'ready_queue_min':8,'empty_events':[],'max_observed_sample_gap_seconds':.1},'completion_witness':{'all_accepted_observed_before_deadline':True},'accepted_task_ids':[str(i) for i in range(n)],'result_verification':{'downloaded_results':n}}
        if c['load_mode']=='mixed_20':raw['auxiliary']={'offered_rate':c['sync_rate'],'successes':5,'capacity_rejections':2,'usage_events':5,'unsent':[]}
        save_private(p/'summary.json',raw);return {'state':'COMPLETE'}
    def arrive(c):
        p=Path(c['output_parent'])/c['run_id'];p.mkdir(mode=0o700);raw={'state':'COMPLETE','measurement_seconds':c['arrival_policy'].get('seconds',1),'accepted':1,'queue_full_rejections':0,'total_p95':.5,'total_p99':1,'target_rate':c['arrival_policy'].get('rate')};save_private(p/'summary.json',raw);return raw
    monkeypatch.setattr(m,'execute',execute);monkeypatch.setattr(m,'execute_arrivals',arrive)
    c={'scope':'C35_FORMAL_CONFIRMATION_AND_FULL_T6','private_mac_backup_receipt_sha256':'c'*64,'run_tag':'unit','template_file':str(template),'claim_proof_file':'unused','output_directory':str(root),'queue_proof_file_sha256':'q'*64,'original_runtime_file_sha256':'a'*64,'fixture_refresh_sha256':'b'*64}
    if interrupt:
        with pytest.raises(RuntimeError,match='synthetic'):m.run(c)
        assert read_private(root/'status-private.json')['state']=='INTERRUPTED_REQUIRES_INSPECTION' and not any(a.get('restoring') for a in actions)
    else:
        s=m.run(c);assert s['state']=='COMPLETE' and s['two_hour_gate_passed'] and s['formal_segment_seconds']>=7200 and len(s['points'])==47
        assert s['restored_parameters']==base and len(s['maintenance'])==1 and any(a.get('executors')==7 for a in actions)
        assert s['production_promoted'] is False and state['parameters']==base
