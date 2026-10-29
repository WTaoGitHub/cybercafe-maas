import copy
import importlib
import json
import sys
import time
import hashlib

import pytest

from maas_bench import matrix
from maas_bench.load import independent_arrivals,prepared_requests
from maas_bench.snapshot import RuntimeFence,read_private,read_secret,save_private


def test_selectors_are_or_within_type_and_and_between_types():
    m=matrix.load()
    stages=matrix.select(m,modules=['native-capacity','async-capacity'],labels=['gpu','changes-pool'])
    assert [s['case_id'] for s in stages]==['T1-NATIVE','T2-ASYNC']
    assert matrix.select(m,case_ids=['T2-ASYNC'],modules=['native-capacity'])==[]
    with pytest.raises(ValueError):matrix.select(m,case_ids=['typo'])


def test_matrix_expands_points_but_blocks_missing_prerequisites():
    m=matrix.load();p=matrix.plan(m,case_ids=['T0-BASELINE','T1-NATIVE','T2-ASYNC'])
    assert [len(s['points']) for s in p['stages']]==[3,12,9]
    assert all(s['planning_state']=='BLOCKED' for s in p['stages'])
    assert len({x['point_id'] for s in p['stages'] for x in s['points']})==24


def test_conditional_skip_only_satisfies_its_named_dependency():
    p=matrix.plan(matrix.load(),case_ids=['T4-QUEUE'],ledger={
        'T3-LIMITS':{'state':'COMPLETE'},'T3-EXTEND':{'state':'SKIPPED_CONDITION_NOT_MET'}})
    assert p['stages'][0]['planning_state']=='ADAPTIVE_SELECTION_REQUIRED'
    assert not p['stages'][0]['execution_implemented']
    p=matrix.plan(matrix.load(),case_ids=['T0-BASELINE'],ledger={'T0-TOOLS':{'state':'SKIPPED_CONDITION_NOT_MET'}})
    assert p['stages'][0]['unresolved_dependencies']==['T0-TOOLS']


def snapshot():return {'model':{'id':5,'replicas':6,'env':{'CPU_THREADS':'2','GPUSTACK_MODEL_VRAM_CLAIM':'2147483648'},'image':'pinned@sha256:abc'},
    'parameters':{'replicas':6},'generation':4,'input_sha256':'abc','app_fixed_env_sha256':'unchanged'}


def test_restore_uses_actual_original_not_a_hardcoded_four():
    original=snapshot();fence=RuntimeFence(original);after=copy.deepcopy(original)
    after['model']['replicas']=8;after['parameters']['replicas']=8;after['generation']=5
    fence.acknowledge(original,after,{'parameters','model','generation'})
    value=fence.restoration(after,paused=True,drained=True,unknown=False,generation=5)
    assert value['model']['replicas']==6 and value['parameters']['replicas']==6


@pytest.mark.parametrize('change',[
    lambda x:x['model'].update(image='other'),
    lambda x:x['model']['env'].update(CPU_THREADS='3'),
    lambda x:x.update(input_sha256='changed'),
    lambda x:x.update(app_fixed_env_sha256='changed'),
])
def test_fence_refuses_unapproved_drift_instead_of_overwriting(change):
    old=snapshot();fence=RuntimeFence(old);after=copy.deepcopy(old);change(after)
    with pytest.raises(RuntimeError):fence.acknowledge(old,after,{'model'})
    with pytest.raises(RuntimeError):fence.restoration(after,paused=True,drained=True,unknown=False,generation=4)


@pytest.mark.parametrize('changes',[{'paused':False},{'drained':False},{'unknown':True},{'generation':8}])
def test_unknown_or_lost_maintenance_ownership_blocks_restore(changes):
    args=dict(paused=True,drained=True,unknown=False,generation=4);args.update(changes)
    old=snapshot()
    with pytest.raises(RuntimeError):RuntimeFence(old).restoration(old,**args)


def test_private_evidence_never_overwrites_previous_run(tmp_path):
    p=tmp_path/'snapshot.json';save_private(p,{'private':'test'})
    assert read_private(p)=={'private':'test'} and p.stat().st_mode & 0o777==0o600
    with pytest.raises(FileExistsError):save_private(p,{'new':'bad'})
    p.chmod(0o644)
    with pytest.raises(ValueError):read_private(p)


def test_tool_secret_reader_is_small_private_and_requires_nonempty_data(tmp_path):
    p=tmp_path/'key';p.write_text(' test-secret\n');p.chmod(0o600)
    assert read_secret(p)=='test-secret'
    p.write_text('')
    with pytest.raises(ValueError):read_secret(p)
    p.write_text('test-secret');p.chmod(0o644)
    with pytest.raises(ValueError):read_secret(p)


def test_cli_dry_run_does_not_import_controller_or_send_inference(tmp_path,monkeypatch,capsys):
    cli=importlib.import_module('maas_bench.__main__')
    p=tmp_path/'config.json';save_private(p,{'run_id':'drytest123','parameters':dict(replicas=6,executors=8,
        global_limit=6,project_limit=6,global_queue=100,project_queue=50),
        'database_dsn_file':'private-not-printed','customer_key_file':'private-not-printed'})
    monkeypatch.setattr(sys,'argv',['maas_bench','run-async','--config',str(p)])
    monkeypatch.setattr(cli,'execute',lambda _:pytest.fail('execution during dry run'))
    assert cli.main()==0
    result=capsys.readouterr().out
    assert 'DRY_RUN' in result and 'private-not-printed' not in result


def test_prepared_request_driver_keeps_a_bounded_pool_and_separates_tail():
    def invoke(item,n):time.sleep(.03);return 'SUCCESS'
    result=prepared_requests(invoke,['ready-input'],6,.1)
    assert result['state']=='COMPLETE' and result['clients']==6
    assert result['measured_completions']>=12 and result['tail_completions']>0
    assert result['continuous_supply_proven'] is False


def test_independent_arrival_clock_does_not_slow_down_to_completed_rate():
    def invoke(n):time.sleep(.05);return 'SUCCESS'
    result=independent_arrivals(invoke,100,.12,2)
    assert result['target_arrivals']==12 and result['state']=='INCONCLUSIVE_GENERATOR'
    assert len(result['unsent'])>0
    assert result['actual_completed_calls']+len(result['unsent'])==12
    assert all(r['reason']=='CLIENT_BUDGET_BUSY' for r in result['unsent'])


def test_request_failure_stops_new_open_loop_scheduling():
    calls=[]
    def invoke(n):calls.append(n);raise TimeoutError()
    result=independent_arrivals(invoke,50,.2,4)
    assert result['state']=='INTERRUPTED_REQUIRES_INSPECTION'
    assert calls==[0] and result['not_scheduled_after_stop']>0


def test_execution_requires_matching_point_and_hashed_prerequisites(tmp_path):
    m=matrix.load();p=tmp_path/'proof.json'
    p.write_text(json.dumps({'case_id':'T0-TOOLS','state':'COMPLETE'}))
    config={'case_id':'T0-BASELINE','point_id':'T0-BASELINE-r1','profile_id':m['profile_id'],
        'parameters':m['baseline'],'completed_stages':{'T0-TOOLS':{'state':'COMPLETE',
            'proof_file':str(p),'proof_sha256':hashlib.sha256(p.read_bytes()).hexdigest()}}}
    matrix.validate_execution(config,'inputs')
    bad=copy.deepcopy(config);bad['parameters']['replicas']=6
    with pytest.raises(ValueError):matrix.validate_execution(bad,'inputs')
    p.write_text('{}')
    with pytest.raises(ValueError):matrix.validate_execution(config,'inputs')


def test_different_model_profile_cannot_supply_stage_evidence(tmp_path):
    m=matrix.load();p=tmp_path/'proof.json'
    p.write_text(json.dumps({'case_id':'T1-NATIVE','state':'COMPLETE','profile_id':m['profile_id'],'input_sha256':'different'}))
    point=matrix.plan(m,case_ids=['T2-ASYNC'])['stages'][0]['points'][0]
    config={'case_id':'T2-ASYNC','point_id':point['point_id'],'profile_id':m['profile_id'],
        'parameters':{k:v for k,v in point.items() if k!='point_id'},'completed_stages':{
            'T1-NATIVE':{'state':'COMPLETE','proof_file':str(p),'proof_sha256':hashlib.sha256(p.read_bytes()).hexdigest()}}}
    with pytest.raises(ValueError):matrix.validate_execution(config,'inputs')


def test_runtime_probe_ignores_utilization_but_tracks_native_gpu_identity(tmp_path):
    from types import SimpleNamespace
    from maas_bench.runtime import ControllerProbe
    catalog=tmp_path/'catalog.json';catalog.write_text('{}')
    (tmp_path/'app.env').write_text('MAAS_CATALOG_PATH='+str(catalog)+'\nMAAS_MAX_RUNNING=4\nPRIVATE_UNRELATED=test\n')
    model={'id':5,'replicas':4,'ready_replicas':4,'env':{'GPUSTACK_MODEL_VRAM_CLAIM':'2147483648'}}
    worker={'id':9,'state':'ready','status':{'gpu_devices':[{'uuid':'fake-gpu','name':'test','core':{'utilization_rate':50}}]}}
    instances=[{'id':i,'worker_id':9,'state':'running'} for i in range(4)]
    def api(path):
        if path=='/version':return {'version':'2.2.3'}
        if path.endswith('/instances'):return {'items':instances}
        if path.startswith('/v2/models'):return model
        return worker
    probe=object.__new__(ControllerProbe);probe.model_id=5;probe.profile_id='unit';probe.inputs='fixed';probe.api=api
    app={'name':'test','service':'executor','global_limit':4,'project_limit':4,'global_queue':1000,'project_queue':100}
    probe.pool=SimpleNamespace(LIVE=tmp_path,inventory=lambda:[dict(app,name='test'+str(i)) for i in range(4)],
        cli=lambda _:dict(paused=False,backend_activity=[],sync_activity=[],generation=4))
    before=probe.capture();fence=RuntimeFence(before)
    worker['status']['gpu_devices'][0]['core']['utilization_rate']=10
    fence.check(probe.capture())
    worker['status']['gpu_devices'][0]['uuid']='different-gpu'
    with pytest.raises(RuntimeError):fence.check(probe.capture())
