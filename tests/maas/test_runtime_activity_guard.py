"""Live capacity tools must stop before adding work after a safety violation."""
import sys
from pathlib import Path
import pytest

sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'scripts/maas/runtime'))
from benchmark_executors import check_activity


def sample(**changes):
    return dict(running=2,sync_running=2,unknown=0,sync_unknown=0,
                paused=False,generation=50,**changes)


def test_sync_and_async_share_one_quota():
    check_activity(sample(),4,50)
    with pytest.raises(RuntimeError,match='quota'):check_activity(sample(),3,50)


@pytest.mark.parametrize('field',['unknown','sync_unknown'])
def test_either_unknown_stops_dispatch(field):
    value=sample();value[field]=1
    with pytest.raises(RuntimeError,match='UNKNOWN'):check_activity(value,4,50)


@pytest.mark.parametrize('field,value',[('paused',True),('generation',51)])
def test_external_maintenance_is_not_taken_over(field,value):
    activity=sample();activity[field]=value
    with pytest.raises(RuntimeError,match='Maintenance'):check_activity(activity,4,50)
