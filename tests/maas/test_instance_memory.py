import pytest
from maas_bench.instance_memory import compute_apps,container_id,sampled_container_totals,candidate_claim,MIB,GIB
from maas_bench.resource_screen import points


def test_parse_and_reject_unavailable_or_foreign_process_memory():
    assert compute_apps('GPU-abc, 12, 2900\n','GPU-abc')[0]['gpu_memory_bytes']==2900*MIB
    for raw in ('GPU-abc, 12, N/A','GPU-other, 12, 4','GPU-abc, 1, 4\nGPU-abc, 1, 4','GPU-abc, 12'):
        with pytest.raises(ValueError):compute_apps(raw,'GPU-abc')


def test_cgroup_mapping_handles_both_docker_layouts_without_guessing():
    cid='a'*64
    assert container_id('0::/system.slice/docker-'+cid+'.scope\n')==cid
    assert container_id('4:memory:/docker/'+cid+'\n')==cid
    for value in ('0::/user.slice/unknown','a'*64+'/'+'b'*64):
        with pytest.raises(ValueError):container_id(value)


def test_budget_sums_all_cuda_processes_of_same_container_first():
    totals=sampled_container_totals([{'container_id':'one','gpu_memory_bytes':2*GIB},{'container_id':'one','gpu_memory_bytes':GIB}])
    assert totals=={'one':3*GIB}
    assert candidate_claim(totals['one'])==int(3.75*GIB)
    assert candidate_claim(2800*MIB)==int(3.5*GIB)
    assert candidate_claim(100*MIB)==768*MIB
    with pytest.raises(ValueError):candidate_claim(0)


def test_single_tier_reuses_identical_bounded_policy_without_enabling_higher_tiers():
    assert points([8])==[points()[2]]
    for tiers in ([],[10],[8,4],[4,4],[True],(8,)):
        with pytest.raises(ValueError):points(tiers)


def test_zombie_rss_disappearance_is_a_recorded_exit_race_only():
    from maas_bench.instance_memory import process_memory
    assert process_memory({'State':'Z (zombie)'}) is None
    assert process_memory({'State':'X (dead)'}) is None
    assert process_memory({'State':'S (sleeping)','VmRSS':'123 kB','VmHWM':'456 kB'})=={'rss_bytes':123*1024,'lifetime_peak_rss_bytes':456*1024}
    for status in ({'State':'R (running)'},{'State':'S','VmRSS':'N/A','VmHWM':'456 kB'}):
        with pytest.raises(ValueError,match='unavailable RSS'):process_memory(status)
