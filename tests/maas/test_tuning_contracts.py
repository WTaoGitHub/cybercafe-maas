import importlib.util
import json
from pathlib import Path
import sys
import threading
import pytest
from maas_bench.contracts import Parameters,integer

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'scripts/maas/runtime'))
from benchmark_executors import bounded_map
from replica_capacity_matrix import planned_points,change_model


@pytest.mark.parametrize('n',[4,6,8,12,16])
def test_independent_executor_and_admission_values(n):
    p=Parameters(6,n,8,4,1000,100)
    assert p.async_slots==min(n,4) and p.effective_queue==100


@pytest.mark.parametrize('bad',[True,0,-1,17,4.0,'6'])
def test_invalid_parallelism_never_submits(bad):
    with pytest.raises(ValueError):bounded_map(lambda _:pytest.fail('sent'),[1],bad)


def test_twelve_inflight_calls_are_drained_without_dispatching_the_remainder():
    stop=threading.Event();barrier=threading.Barrier(12);called=[]
    def task(i):
        called.append(i);barrier.wait(5)
        if i==0:stop.set();raise RuntimeError('failed')
        stop.wait(5)
    with pytest.raises(RuntimeError):bounded_map(task,range(200),12,stop)
    assert sorted(called)==list(range(12))


def test_six_replicas_and_twelve_clients_are_explicit_points():
    points=planned_points('tuning',[21,22],[4,6,8],[4,6,8,12],1)
    assert len(points)==12 and any(n==6 and c==12 for _,n,_,c in points)
    assert planned_points('remaining',[21,22],[4,6,8],[8],1)[0][3]==8


def test_model_change_uses_its_runtime_identity():
    before={'id':42,'replicas':6,'worker_selector':{},'env':{'claim':'keep'}};calls=[]
    def api(path,data=None,method=None):
        calls.append((path,method));return data if method=='PUT' else before
    assert change_model(api,before,12,{})['replicas']==12
    assert calls==[('/v2/models/42',None),('/v2/models/42','PUT')]


def test_queue_settings_preserve_unrelated_private_environment(tmp_path,monkeypatch):
    spec=importlib.util.spec_from_file_location('tuning_pool',ROOT/'scripts/maas/runtime/executor_pool.py')
    pool=importlib.util.module_from_spec(spec);spec.loader.exec_module(pool)
    monkeypatch.setattr(pool,'LIVE',tmp_path)
    p=tmp_path/'app.env';p.write_text('UNRELATED=retain\nMAAS_MAX_RUNNING=4\nMAAS_MAX_QUEUED=1000\n')
    pool.set_count(12,(12,8),(200,100))
    values=dict(x.split('=',1) for x in p.read_text().splitlines())
    assert values['UNRELATED']=='retain' and values['MAAS_EXECUTOR_REPLICAS']=='12'
    assert values['MAAS_MAX_RUNNING']=='12' and values['MAAS_MAX_PROJECT_RUNNING']=='8'
    assert values['MAAS_MAX_QUEUED']=='200' and values['MAAS_MAX_PROJECT_QUEUED']=='100'
    assert p.stat().st_mode & 0o777==0o600


@pytest.mark.parametrize('args',[
    ['--replicas','17'],['--replicas','8','--global-queue','20'],
    ['--replicas','8','--global-queue','10','--project-queue','20']])
def test_pool_invalid_expansions_fail_before_opening_a_maintenance_window(args,monkeypatch):
    spec=importlib.util.spec_from_file_location('invalid_pool',ROOT/'scripts/maas/runtime/executor_pool.py')
    pool=importlib.util.module_from_spec(spec);spec.loader.exec_module(pool)
    monkeypatch.setattr(sys,'argv',['executor_pool.py','scale',*args])
    monkeypatch.setattr(pool,'inventory',lambda:pytest.fail('inspected before argument validation'))
    with pytest.raises(SystemExit):pool.main()
