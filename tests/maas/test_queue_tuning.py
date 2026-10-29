import importlib.util
from pathlib import Path
import pytest
from maas_bench.contracts import Parameters


def load(name):
    path=Path(__file__).resolve().parents[2]/'scripts/maas/runtime'/name
    spec=importlib.util.spec_from_file_location(name,path);mod=importlib.util.module_from_spec(spec);spec.loader.exec_module(mod);return mod


def test_queue_sweep_has_explicit_boundaries_and_independent_rates():
    m=load('queue_tuning.py');base=Parameters(8,12,12,12,1000,100).to_dict()
    points=m.planned({'parameters':base,'screening_throughput':20})
    assert len(points)==45 and len({r['point_id'] for r in points})==45
    assert {p['parameters']['project_queue'] for p in points}=={10,25,50,100,200}
    normal=[p for p in points if p.get('fraction') is not None]
    assert len(normal)==20 and all(p['arrival_policy']['seconds']==180 for p in normal)
    assert {p['arrival_policy']['rate'] for p in normal}=={10,16,20,24}

