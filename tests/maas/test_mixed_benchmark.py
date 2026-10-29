"""Mixed live evidence must include both accepted work and non-metered rejections."""
import sys
from pathlib import Path
import pytest

sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'scripts/maas/runtime'))
from benchmark_mixed_ingress import verify_counts


def evidence():
    records=[{'kind':'async'} for _ in range(24)]
    records += [{'kind':'sync','http_status':200} for _ in range(20)]
    records += [{'kind':'sync','http_status':429} for _ in range(16)]
    tasks=[{'state':'SUCCEEDED','attempts':1,'events':1} for _ in range(24)]
    sync=[{'state':'COMPLETED','outcome':'SUCCEEDED','events':1} for _ in range(20)]
    return records,tasks,sync


def test_quota_rejections_have_no_sync_activity_or_success_events():
    verify_counts(*evidence())


def test_rejected_request_creating_an_event_is_not_accepted():
    records,tasks,sync=evidence();sync.append(sync[0].copy())
    with pytest.raises(RuntimeError,match='Sync window'):verify_counts(records,tasks,sync)


def test_other_sync_traffic_prevents_window_attribution():
    records,tasks,sync=evidence();sync.pop()
    with pytest.raises(RuntimeError,match='Sync window'):verify_counts(records,tasks,sync)


def test_duplicate_async_metering_is_not_a_success():
    records,tasks,sync=evidence();tasks[0]['events']=2
    with pytest.raises(RuntimeError,match='Async result'):verify_counts(records,tasks,sync)


def test_no_quota_rejection_is_inconclusive():
    records,tasks,sync=evidence()
    for r in records:
        if r['kind']=='sync':r['http_status']=200
    with pytest.raises(RuntimeError,match='Both sync'):verify_counts(records,tasks,sync)
