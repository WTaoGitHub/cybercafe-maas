import copy
import json
from types import SimpleNamespace
import pytest
from maas_bench.controller import fixed_profile,PilotController
from maas_bench.contracts import Parameters
from maas_bench.resources import ResourceGuard,ResourcePressure


def profile():return {'profile_id':'test','input_sha256':'input','model':{'id':1,'replicas':4,'env':{'threads':'2'}},
    'gpustack_version':'2.2.3','workers':[{'id':1,'uuid':'gpu'}],'catalog_sha256':'catalog',
    'app_fixed_env_sha256':'fixed','app_containers':[{'service':'executor','image':'same','id':'old'}]}


def test_fixed_profile_allows_only_expected_volatile_count_fields():
    a=profile();b=copy.deepcopy(a);b['model']['replicas']=8;b['app_containers']*=2
    b['app_containers'][0]['id']='new'
    assert fixed_profile(a)==fixed_profile(b)
    b['model']['env']['threads']='3';assert fixed_profile(a)!=fixed_profile(b)


def hardware():return [{'id':1,'ram_total':32*1024**3,'gpus':[{'memory_total':16*1024**3}]}]
def readings():return [{'worker_id':1,'worker_node_gram_used_bytes':4*1024**3,'worker_node_memory_used_bytes':8*1024**3,'worker_node_cpu_utilization_rate':10}]
def host():return {'cpu_percent':10,'available_ram_bytes':24*1024**3,'total_ram_bytes':32*1024**3}


def test_resource_guard_requires_headroom_but_allows_busy_gpu():
    guard=ResourceGuard(hardware());guard.check(readings(),host(),0)
    rows=readings();rows[0]['worker_node_gram_used_bytes']=15*1024**3
    with pytest.raises(ResourcePressure,match='GPU'):guard.check(rows,host(),1)


def test_sustained_cpu_is_timed_per_machine_and_reset_by_recovery():
    guard=ResourceGuard(hardware());h=host();h['cpu_percent']=85
    guard.check(readings(),h,0);guard.check(readings(),h,29)
    with pytest.raises(ResourcePressure,match='CPU'):guard.check(readings(),h,30)
    guard.check(readings(),host(),31);guard.check(readings(),h,32);guard.check(readings(),h,60)


def test_low_controller_ram_stops_experiment():
    h=host();h['available_ram_bytes']=1024**3
    with pytest.raises(ResourcePressure,match='RAM'):ResourceGuard(hardware()).check(readings(),h,0)


def controller_fixture(tmp_path):
    snapshot=profile();snapshot.update(parameters=Parameters(6,4,4,4,1000,100).to_dict(),generation=10)
    snapshot['model']['replicas']=6
    state=copy.deepcopy(snapshot)
    c=object.__new__(PilotController);c.original=copy.deepcopy(snapshot);c.expected=copy.deepcopy(snapshot)
    c.output=tmp_path;c.fixed=fixed_profile(snapshot);c.images=[]
    c.probe=SimpleNamespace(capture=lambda:copy.deepcopy(state),root=tmp_path)
    c.image_ids=lambda _:[]
    c.quiet=lambda:None
    return c,state


def test_controller_rejects_external_drift_before_touching_parameters(tmp_path):
    c,state=controller_fixture(tmp_path);state['generation']+=1
    c._model_replicas=lambda *_:pytest.fail('mutated after drift')
    with pytest.raises(RuntimeError,match='drift'):c.change(Parameters(8,4,4,4,1000,100))
    assert not list(tmp_path.iterdir())


def test_failed_pool_change_keeps_failure_journal_without_automatic_restore(tmp_path,monkeypatch):
    c,state=controller_fixture(tmp_path);calls=[]
    def failed(cmd,**kw):calls.append(cmd);return SimpleNamespace(returncode=1,stdout='',stderr='isolated failure')
    monkeypatch.setattr('maas_bench.controller.subprocess.run',failed)
    with pytest.raises(RuntimeError,match='transition failed'):c.change(Parameters(6,8,8,8,1000,100))
    assert len(calls)==1 and state['parameters']['executors']==4
    op=next(tmp_path.glob('*.json'))
    assert json.loads(op.read_text())['state']=='INTERRUPTED_REQUIRES_INSPECTION'


def test_restore_targets_actual_six_replica_origin_not_four(tmp_path):
    c,state=controller_fixture(tmp_path);seen=[]
    c.change=lambda p:seen.append(p.to_dict())
    c.restore()
    assert seen[0]['replicas']==6
