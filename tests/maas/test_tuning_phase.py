from types import SimpleNamespace
import threading
import pytest
from maas_bench.phase import Phase


def fake_phase(tmp_path,changed,count=0):
    p=object.__new__(Phase);p.config={'run_tag':'r1'};p.output=tmp_path;p.template={'output_parent':str(tmp_path/'runs'),'project':'one'}
    p.observer=SimpleNamespace(lock=threading.Lock(),connection=SimpleNamespace(execute=lambda *a:SimpleNamespace(fetchone=lambda:{'n':count})))
    p.controller=SimpleNamespace(change=lambda _:changed.append(True));return p


def test_existing_output_stops_before_parameter_change(tmp_path):
    changed=[];p=fake_phase(tmp_path,changed);(tmp_path/'existing-point-r1-before-private.json').touch()
    with pytest.raises(FileExistsError):p.point_config({},'existing-point')
    assert not changed


def test_existing_database_namespace_stops_before_parameter_change(tmp_path):
    changed=[];p=fake_phase(tmp_path,changed,count=1)
    with pytest.raises(ValueError,match='namespace'):p.point_config({},'existing-point')
    assert not changed


def test_invalid_attempt_tag_stops_before_parameter_change(tmp_path):
    changed=[];p=fake_phase(tmp_path,changed);p.config['run_tag']='../bad'
    with pytest.raises(ValueError):p.point_config({},'existing-point')
    assert not changed
