from types import SimpleNamespace
import pytest
from maas_bench.resources import observe_resources,ResourcePressure
from maas_bench.resource_screen import points,memory_plateau


def test_resource_failure_reading_is_emitted_before_enforcement():
    readings=[{'worker_id':17,'worker_node_gram_used_bytes':15*1024**3}]
    def fail(*args):raise ResourcePressure('GPU_HEADROOM')
    guard=SimpleNamespace(controller=lambda:{'cpu_percent':1},check=fail)
    probe=SimpleNamespace(resources=lambda _:readings);events=[]
    with pytest.raises(ResourcePressure,match='GPU_HEADROOM'):
        observe_resources(guard,probe,{},events.append)
    assert len(events)==1 and events[0]['kind']=='resources'
    assert events[0]['workers']==readings and events[0]['at_monotonic']>0


def test_diagnostic_tiers_bound_residency_and_execution_together():
    rows=points();assert [x['replicas'] for x in rows]==[4,6,8]
    for p in rows:
        assert p['replicas']==p['executors']==p['global_limit']==p['project_limit']
        assert p['global_queue']==p['project_queue']==25
        assert p['vram_claim_bytes']==2*1024**3


def memory_rows(growth=0):
    return [{'at_monotonic':i,'workers':[{'worker_id':1,'worker_node_gram_used_bytes':
        8*1024**3+(growth if i>=165 else 0)}]} for i in range(181)]


def test_last_minute_plateau_is_not_confused_with_late_resident_growth():
    workers=[{'id':1,'gpus':[{'memory_total':16*1024**3}]}]
    result=memory_plateau(memory_rows(),workers,180)[0]
    assert result['plateau_observed'] and result['minimum_logged_free_bytes']==8*1024**3
    growing=memory_plateau(memory_rows(1024**3),workers,180)[0]
    assert not growing['plateau_observed'] and growing['high_water_growth_bytes']==1024**3


def test_plateau_cannot_be_inferred_without_both_last_minute_halves():
    workers=[{'id':1,'gpus':[{'memory_total':16*1024**3}]}]
    with pytest.raises(ValueError,match='Insufficient'):
        memory_plateau(memory_rows()[-10:],workers,180)


def test_explicit_diagnostic_scope_rejects_accidental_formal_execution():
    from test_queue_tuning import load
    m=load('resource_screen.py')
    with pytest.raises(ValueError,match='diagnostic scope'):
        m.run({'diagnostic_kind':'T6-SOAK'})
    with pytest.raises(ValueError,match='backup receipt'):
        m.run({'diagnostic_kind':'CONCURRENT_RESOURCE_SCREEN_NOT_T6_PROOF'})
