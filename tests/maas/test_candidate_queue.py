import copy
import pytest
from maas_bench.candidate_queue import planned,compatible_runtime


def proof():
    return {'parameters':{'replicas':8,'executors':8,'global_limit':8,'project_limit':8,'global_queue':25,'project_queue':25,'vram_claim_bytes':int(3.5*1024**3)},'reference_mu':{'ordinary':16,'mixed':12}}


def test_fixed_q25_confirmation_uses_own_workload_rates_and_three_normal_repeats():
    rows=planned(proof());assert len(rows)==28 and len({x['point_id'] for x in rows})==28
    for kind,mu in [('ordinary',16),('mixed',12)]:
        own=[r for r in rows if r['workload']==kind];normal=[r for r in own if r['mode']=='independent_arrivals' and r['qos_class']=='normal']
        assert len(normal)==6 and {r['arrival_policy']['rate'] for r in normal}=={mu*.5,mu*.8}
        assert all(r['arrival_policy']['seconds']==180 for r in normal)
        assert {r['arrival_policy']['count'] for r in own if r['qos_class']=='bounded_burst'}=={25,50,100,200}
        assert own[-2]['arrival_policy']['rate']==mu*1.2 and own[-1]['phase_kind']=='recovery' and own[-1]['arrival_policy']=={'count':3,'clients':1}
        assert all(r['parameters']['project_queue']==r['parameters']['global_queue']==25 for r in own)


def test_runtime_binding_does_not_require_old_container_ids_but_rejects_hardware_or_image_drift():
    from test_claim_transition import fixture
    _,_,source,current=fixture();current['model']['env']['GPUSTACK_MODEL_VRAM_CLAIM']=str(int(3.5*1024**3))
    compatible_runtime(source,current)
    for key in ('workers','catalog_sha256','app_fixed_env_sha256'):
        bad=copy.deepcopy(current);bad[key]='changed'
        with pytest.raises(ValueError,match='drift'):compatible_runtime(source,bad)
    current['app_containers'][0]['image']='other'
    with pytest.raises(ValueError,match='image drift'):compatible_runtime(source,current)


def fixture_files(tmp_path,monkeypatch):
    import json,hashlib
    from maas_bench.snapshot import save_private,digest
    from maas_bench.candidate_qualification import planned as qual_plan
    from maas_bench.candidate_queue import PROTOCOL,CASE
    from test_claim_transition import fixture
    c={'profile_id':'p'};ordinary={'fixtures':[]};path=tmp_path/'fixture.json';save_private(path,ordinary);c['fixture_file']=str(path)
    p=proof();p.update(case_id='CANDIDATE-QUALIFICATION-35',state='COMPLETE',profile_id='p',input_sha256=digest(ordinary),points=[])
    points=tmp_path/'source';points.mkdir(mode=0o700)
    for i,plan in enumerate(qual_plan()):
        rate=p['reference_mu'][plan['workload']];raw={'state':'COMPLETE','parameters':p['parameters'],'measurement_seconds':plan['seconds'],'measured_successes':rate*plan['seconds'],'throughput_per_second':rate,'supply':{'sample_count':300,'ready_queue_min':8,'empty_events':[],'max_observed_sample_gap_seconds':.1}}
        folder=points/('point-'+str(i));folder.mkdir(mode=0o700);file=folder/'summary-private.json';save_private(file,raw)
        p['points'].append({**plan,**raw,'run_id':'run-'+str(i),'resource_qualified':i==0,'card_memory':[{'plateau_observed':True,'total_bytes':16*1024**3,'minimum_logged_free_bytes':4*1024**3}]*2,'source_summary_sha256':hashlib.sha256(file.read_bytes()).hexdigest()})
    file=tmp_path/'proof.json';save_private(file,p);c.update(candidate_mu_file=str(file),candidate_mu_sha256=hashlib.sha256(file.read_bytes()).hexdigest(),candidate_mu_points_root=str(points))
    snapshot=fixture()[2];snapshot['parameters']=p['parameters'];snapshot['model']['env']['GPUSTACK_MODEL_VRAM_CLAIM']=str(p['parameters']['vram_claim_bytes'])
    file=tmp_path/'runtime.json';save_private(file,snapshot);c.update(candidate_runtime_file=str(file),candidate_runtime_sha256=hashlib.sha256(file.read_bytes()).hexdigest(),snapshot_file=str(file))
    selected=planned(p);selection={'protocol':PROTOCOL,'points':selected,'candidate_mu_sha256':c['candidate_mu_sha256'],'qos_policy_sha256':'policy','workload_sha256':{'ordinary':'ordinary-hash','mixed':'mixed-hash'}}
    file=tmp_path/'selection.json';save_private(file,selection)
    c.update(selection_file=str(file),selection_sha256=hashlib.sha256(file.read_bytes()).hexdigest(),candidate_queue_protocol=PROTOCOL,case_id=CASE,**selected[0],load_mode=selected[0]['mode'],workload_sha256='ordinary-hash')
    monkeypatch.setattr('maas_bench.candidate_queue.load_policy',lambda _:{'_sha256':'policy'})
    return c,ordinary


def test_explicit_new_proof_is_accepted_without_impersonating_legacy_stages(tmp_path,monkeypatch):
    from maas_bench.candidate_queue import validate_execution
    c,m=fixture_files(tmp_path,monkeypatch);assert validate_execution(c,m)=={'_sha256':'policy'}
    for key,value in [('case_id','T4-QUEUE'),('candidate_queue_protocol','unknown'),('workload','mixed'),('load_mode','bounded_burst'),('arrival_policy',{'seconds':180,'rate':999,'clients':16})]:
        bad=copy.deepcopy(c);bad[key]=value
        with pytest.raises(ValueError):validate_execution(bad,m)


def test_source_summary_or_selection_tampering_is_rejected(tmp_path,monkeypatch):
    from pathlib import Path
    from maas_bench.candidate_queue import validate_execution
    c,m=fixture_files(tmp_path,monkeypatch);Path(c['selection_file']).write_text('{}')
    with pytest.raises(ValueError,match='selection changed'):validate_execution(c,m)
    Path(c['candidate_mu_points_root'],'point-1','summary-private.json').write_text('{}')
    with pytest.raises(ValueError,match='source summary changed'):validate_execution(c,m)


def test_live_entry_requires_scope_and_backup_before_access():
    from test_queue_tuning import load
    m=load('confirm_candidate_queue.py')
    with pytest.raises(ValueError,match='scope'):m.run({'scope':'T6'})
    with pytest.raises(ValueError,match='backup'):m.run({'scope':'Q25_CONFIRMATION_AND_HTTP_RECOVERY_NOT_T6'})
