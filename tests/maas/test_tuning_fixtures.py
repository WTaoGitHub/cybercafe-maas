import pytest
from maas_bench.fixtures import prepared_fixtures
from maas_bench.snapshot import save_private,digest


def test_mixed_workload_is_separately_hashed_and_not_presented_as_ordinary(tmp_path):
    ordinary={'fixtures':[{'id':'a','input':{}}]};mixed={'fixtures':[{'id':'b','input':{}}],'workload':'ordinary80_boundary20'}
    work=tmp_path/'mixed.json';quality=tmp_path/'quality.json';save_private(work,mixed)
    save_private(quality,{'profile_id':'test','input_sha256':digest(mixed),'items':{'b':{'text_sha256':'sha'}}})
    cfg={'profile_id':'test','workload_fixture_file':str(work),'workload_sha256':digest(mixed),'expected_text_file':str(quality)}
    fixtures,scope=prepared_fixtures(cfg,ordinary)
    assert fixtures[0]['id']=='b' and scope['workload_kind']=='ordinary80_boundary20' and scope['workload_sha256']!=digest(ordinary)
    cfg['workload_sha256']=digest(ordinary)
    with pytest.raises(ValueError,match='changed'):prepared_fixtures(cfg,ordinary)


def test_quality_reference_must_match_the_actual_workload(tmp_path):
    ordinary={'fixtures':[{'id':'a','input':{}}]};p=tmp_path/'quality.json'
    save_private(p,{'profile_id':'test','input_sha256':'other','items':{}})
    with pytest.raises(ValueError,match='quality'):prepared_fixtures({'profile_id':'test','expected_text_file':str(p)},ordinary)
