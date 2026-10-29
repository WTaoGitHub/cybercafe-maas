from test_queue_tuning import load
from maas_bench.contracts import Parameters


def test_soak_has_24_full_five_minute_segments_plus_separate_bursts_and_probes():
    m=load('soak_tuning.py');p=Parameters(8,12,12,12,25,25).to_dict();points=m.planned(p,'workload',20)
    segments=[r for r in points if 'segment' in r]
    assert len(segments)==24 and sum(r['mode']=='mixed_20' for r in segments)==6
    assert sum(r['mode']=='async_saturation' for r in segments)==6
    assert sum(r['mode']=='bounded_burst' for r in points)==8
    assert all(r['workload_sha256']=='workload' and r['parameters']==p for r in points)
    assert {r['arrival_policy']['rate'] for r in segments if r['mode']=='independent_arrivals'}=={10}
