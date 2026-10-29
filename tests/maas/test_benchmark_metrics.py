"""A zero-replica allocation gauge can be absent without a transport failure."""
from pathlib import Path
import sys
import pytest

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'scripts/maas/runtime'))
from benchmark_executors import worker_metrics

USED='worker_node_gram_used_bytes'
ALLOCATED='worker_node_gram_allocated_bytes'


def test_optional_allocation_missing_is_unknown_not_zero_or_connection_error():
    values=worker_metrics(['gpustack:'+USED+'{gpu="single"} 1234'],[USED,ALLOCATED])
    assert values=={USED:1234,ALLOCATED:None}


def test_exported_zero_differs_from_an_absent_gauge():
    values=worker_metrics(['gpustack:'+ALLOCATED+'{gpu="single"} 0'],[ALLOCATED])
    assert values[ALLOCATED]==0


def test_required_metric_missing_remains_an_error():
    with pytest.raises(ValueError,match='Required'):
        worker_metrics([], [USED,ALLOCATED])


@pytest.mark.parametrize('value',['NaN','+Inf','-Inf','invalid'])
def test_invalid_metric_is_not_silently_accepted(value):
    with pytest.raises(ValueError):worker_metrics(['gpustack:'+USED+' '+value],[USED])


@pytest.mark.parametrize('labels',['','{gpu="NVIDIA GeForce test"}'])
def test_labeled_and_unlabeled_samples_with_optional_timestamp(labels):
    assert worker_metrics(['gpustack:'+USED+labels+' 42 12345'],[USED])[USED]==42
