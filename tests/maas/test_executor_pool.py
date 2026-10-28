import importlib.util,json
from pathlib import Path
import pytest
spec=importlib.util.spec_from_file_location('executor_pool',Path(__file__).resolve().parents[2]/'scripts/maas/runtime/executor_pool.py')
pool=importlib.util.module_from_spec(spec);spec.loader.exec_module(pool)


@pytest.fixture
def setup_pool(tmp_path,monkeypatch):
    monkeypatch.setattr(pool,'LIVE',tmp_path);monkeypatch.setattr(pool,'LOCK',tmp_path/'lock')
    monkeypatch.setattr(pool.os,'getuid',lambda:0);monkeypatch.setattr(pool.socket,'gethostname',lambda:'XZ-31-001')
    monkeypatch.setattr('sys.argv',['executor_pool.py','scale','--replicas','2'])
    monkeypatch.setattr(pool,'inventory',lambda:[])
    monkeypatch.setattr(pool,'set_count',lambda _:pytest.fail('Configuration changed before safe drain'))
    monkeypatch.setattr(pool,'run',lambda *_:pytest.fail('Container mutation before safe drain'))
    return tmp_path


def test_pool_scale_does_not_take_over_existing_pause(setup_pool,monkeypatch):
    monkeypatch.setattr(pool,'cli',lambda *a:{'paused':True})
    with pytest.raises(RuntimeError,match='Already paused'):pool.main()
    assert not (setup_pool/'executors/pending.json').exists()


def test_unknown_keeps_pause_and_never_scales_or_resumes(setup_pool,monkeypatch):
    calls=[]
    def cli(action,*args):
        calls.append(action)
        if action=='pause':return {'generation':1}
        return {'paused':len(calls)>1,'generation':1,'backend_activity':[{'state':'UNKNOWN'}],'sync_activity':[]}
    monkeypatch.setattr(pool,'cli',cli)
    with pytest.raises(RuntimeError,match='UNKNOWN'):pool.main()
    assert 'resume' not in calls
    assert json.loads((setup_pool/'executors/pending.json').read_text())['requires_operator_review']


def test_changed_generation_is_not_overwritten(setup_pool,monkeypatch):
    calls=[]
    def cli(action,*args):
        calls.append(action)
        if action=='pause':return {'generation':1}
        return {'paused':len(calls)>1,'generation':2,'backend_activity':[],'sync_activity':[]}
    monkeypatch.setattr(pool,'cli',cli)
    with pytest.raises(RuntimeError,match='ownership'):pool.main()
    assert 'resume' not in calls


def test_pool_rejects_project_limit_above_global_before_any_mutation(setup_pool,monkeypatch):
    monkeypatch.setattr('sys.argv',['executor_pool.py','scale','--replicas','4','--global-limit','2','--project-limit','4'])
    with pytest.raises(SystemExit):pool.main()
    assert not (setup_pool/'executors/pending.json').exists()
