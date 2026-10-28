"""Maintenance actuator refuses arbitrary SSH and never confuses worker/GPU stop."""
import json
from types import SimpleNamespace
import pytest
from maas_bootstrap import node_control as n


@pytest.mark.parametrize('command',['reboot','status; shutdown now','stop invalid','status extra extra','docker stop x',''])
def test_forced_command_rejects_unsupported_before_identity_lookup(monkeypatch,command):
    monkeypatch.setattr('sys.argv',['node_control.py','ssh'])
    monkeypatch.setenv('SSH_ORIGINAL_COMMAND',command)
    monkeypatch.setattr(n,'guard',lambda:pytest.fail('Rejected command reached node guard'))
    with pytest.raises(RuntimeError):n.main()


def test_stopped_worker_does_not_mean_gpu_idle(monkeypatch):
    def run(*args,**kw):
        if args[:3]==('docker','ps','-q'):return SimpleNamespace(stdout='')
        if args[0]=='nvidia-smi':return SimpleNamespace(stdout='123, python3, 900\n')
        raise AssertionError(args)
    monkeypatch.setattr(n,'run',run)
    v=n.inventory()
    assert not v['running_worker_containers'] and v['gpu_compute_processes']
    assert not v['backend_stop_observed'] and not v['safe_to_poweroff']


def test_unparseable_gpu_evidence_is_not_an_idle_proof(monkeypatch):
    monkeypatch.setattr(n,'run',lambda *a,**k:SimpleNamespace(stdout='' if a[0]=='docker' else 'N/A\n'))
    with pytest.raises(RuntimeError,match='inventory'):n.inventory()
