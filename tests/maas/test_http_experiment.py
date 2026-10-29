import pytest
from maas_bench.http_experiment import window_summary


def test_http_statuses_and_tail_completions_are_never_combined_into_202_capacity():
    r={'state':'COMPLETE','measurement_seconds':1,'records':[
        {'completed_offset':.5,'outcome':{'http_status':202,'seconds':.2}},
        {'completed_offset':.9,'outcome':{'http_status':429,'seconds':.01}},
        {'completed_offset':1.1,'outcome':{'http_status':202,'seconds':.2}}]}
    v=window_summary(r)
    assert 'records' not in v and v['http_groups']['202']['count']==1 and v['http_groups']['429']['count']==1


def test_authentication_stub_is_rejected_for_capacity_before_any_db_reset():
    from maas_bench.http_experiment import run
    with pytest.raises(ValueError,match='stub'):run({},test_auth_client=object())
