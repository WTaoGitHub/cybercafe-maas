"""No test invokes poweroff/reboot. Shutdown jobs and HTTP are local stand-ins."""
import configparser
import io
import json
from pathlib import Path
import signal
import subprocess
import time
from types import SimpleNamespace
import urllib.error
from uuid import UUID

import pytest

from maas_bootstrap import shutdown_events as module
from maas_bootstrap.client import BootstrapError, atomic_json
from maas_bootstrap.shutdown_auth import issue
from test_shutdown_auth import SCOPE


@pytest.fixture
def sender(tmp_path, monkeypatch):
    root = tmp_path/'state';root.mkdir(mode=0o700)
    key = tmp_path/'key';key.write_text(bytes(range(32)).hex());key.chmod(0o600)
    boot = tmp_path/'boot';boot.write_text(SCOPE['boot_id'])
    cert = tmp_path/'ca';cert.write_text('test public trust material')
    events = {'url':'https://maas.example.invalid'+module.PATH,'tls_certificate_file':str(cert)}
    identity = {k:v for k,v in SCOPE.items() if k != 'boot_id'}
    atomic_json(root/'identity.json',identity)
    prepared = dict(events, scope=SCOPE, credential=issue(key,SCOPE))
    atomic_json(root/'shutdown-event.json',prepared)
    config = tmp_path/'bootstrap.json'
    atomic_json(config,dict(state_dir=str(root),shutdown_events=events))
    monkeypatch.setattr(module,'BOOT_ID',boot)
    monkeypatch.setattr(module,'guard_clone_host',lambda:None)
    monkeypatch.setattr(module,'is_shutdown',lambda deadline:True)
    requests = []
    class Response(io.BytesIO):
        status = 201
    class Opener:
        def open(self, request, timeout):
            requests.append((request,timeout))
            return Response(b'{}')
    monkeypatch.setattr(module,'https_opener',lambda _:Opener())
    return SimpleNamespace(config=config,root=root,boot=boot,key=key,events=events,
                           prepared=prepared,requests=requests)


def test_each_shutdown_sends_one_new_event_and_leaves_local_state_unchanged(sender):
    original = {p.name:p.read_bytes() for p in sender.root.iterdir()}
    for _ in range(2):
        assert module.notify(sender.config) == {'code':'SHUTDOWN_EVENT_SENT'}
    assert len(sender.requests) == 2
    bodies = [json.loads(request.data) for request, _ in sender.requests]
    assert bodies[0]['event_id'] != bodies[1]['event_id']
    assert all(UUID(body['event_id']).version == 4 and {k:body[k] for k in SCOPE} == SCOPE for body in bodies)
    assert all(request.method == 'POST' and 0 < timeout <= 1 for request,timeout in sender.requests)
    assert all(request.get_header('Authorization') == 'Bearer '+sender.prepared['credential'] for request,_ in sender.requests)
    assert original == {p.name:p.read_bytes() for p in sender.root.iterdir()}


def test_service_stop_missing_config_or_changed_boot_never_sends(sender, monkeypatch):
    monkeypatch.setattr(module,'is_shutdown',lambda deadline:False)
    assert module.notify(sender.config)['code'] == 'SHUTDOWN_EVENT_SKIPPED'
    sender.boot.write_text('11111111-2222-4333-8444-555555555555')
    with pytest.raises(BootstrapError,match='BINDING_CHANGED'):
        module.notify(sender.config)
    atomic_json(sender.config,{'state_dir':str(sender.root)})
    assert module.notify(sender.config)['code'] == 'SHUTDOWN_EVENT_DISABLED'
    assert not sender.requests


@pytest.mark.parametrize('change', [
    {'url':'http://maas.example.invalid'+module.PATH}, {'url':'https://user:password@maas.example.invalid'+module.PATH},
    {'url':'https://maas.example.invalid/other'}, {'url':'https://maas.example.invalid'+module.PATH+'?q=x'},
    {'url':'https://maas.example.invalid'+module.PATH+'#fragment'}, {'tls_certificate_file':'relative'}, {'untrusted':True},
])
def test_invalid_local_destination_is_rejected_before_http(sender, change):
    atomic_json(sender.config,dict(state_dir=str(sender.root),shutdown_events=sender.events|change))
    with pytest.raises(BootstrapError,match='CONFIG_INVALID'):
        module.notify(sender.config)
    assert not sender.requests


def test_private_file_and_delivery_scope_are_checked(sender):
    path = sender.root/'shutdown-event.json'
    path.chmod(0o644)
    with pytest.raises(BootstrapError):
        module.notify(sender.config)
    path.chmod(0o600)
    atomic_json(path,sender.prepared|{'scope':SCOPE|{'worker_id':999}})
    with pytest.raises(BootstrapError,match='BINDING_CHANGED'):
        module.notify(sender.config)
    assert not sender.requests


@pytest.mark.parametrize('status',[301,401,403,409,413,503])
def test_http_error_is_attempted_once_and_never_blocks_shutdown(sender, monkeypatch, capsys, status):
    calls = []
    class Opener:
        def open(self,*a,**k):
            calls.append(1)
            raise urllib.error.HTTPError('https://private.invalid',status,'private-server-error',{},None)
    monkeypatch.setattr(module,'https_opener',lambda _:Opener())
    assert module.main(['--config',str(sender.config)]) == 0
    assert len(calls) == 1
    assert json.loads(capsys.readouterr().out) == {'code':'SHUTDOWN_EVENT_UNAVAILABLE'}
    assert signal.getitimer(signal.ITIMER_REAL)[0] == 0


@pytest.mark.parametrize('phase',['dns','tls','response'])
def test_process_deadline_bounds_blocking_phases_without_retry(sender, monkeypatch, capsys, phase):
    calls = []
    def stall():
        calls.append(phase)
        time.sleep(10)
    if phase == 'tls':
        monkeypatch.setattr(module,'https_opener',lambda _:stall())
    else:
        class Opener:
            def open(self,*a,**kw):
                return stall()
        monkeypatch.setattr(module,'https_opener',lambda _:Opener())
    started = time.monotonic()
    assert module.main(['--config',str(sender.config)]) == 0
    elapsed = time.monotonic()-started
    assert .8 <= elapsed < 1.5 and calls == [phase]
    assert json.loads(capsys.readouterr().out)['code'] == 'SHUTDOWN_EVENT_UNAVAILABLE'


@pytest.mark.parametrize('output,expected',[
    ('123 poweroff.target start waiting\n456 docker.service stop waiting',True),
    ('123 halt.target start running',True), ('123 reboot.target start waiting',False),
    ('123 poweroff.target stop waiting',False), ('123 docker.service stop waiting',False),
    ('malformed poweroff.target start waiting',False), ('',False),
])
def test_real_job_parser_has_no_shutdown_or_restart_commands(monkeypatch, output, expected):
    def run(argv,**kwargs):
        assert argv == ['/usr/bin/systemctl','list-jobs','--plain','--no-legend','--no-pager']
        assert 0 < kwargs['timeout'] <= .1 and kwargs['env']['LC_ALL'] == 'C'
        return SimpleNamespace(returncode=0,stdout=output)
    monkeypatch.setattr(module.subprocess,'run',run)
    assert module.is_shutdown(time.monotonic()+1) is expected


def test_job_query_failure_skips_notifies(monkeypatch):
    def run(*a,**kw):
        raise subprocess.TimeoutExpired('local-job-query',.1)
    monkeypatch.setattr(module.subprocess,'run',run)
    assert not module.is_shutdown(time.monotonic()+1)


def test_template_guard_still_applies_but_xz_001_is_normal_worker(sender, monkeypatch):
    from maas_bootstrap import worker
    monkeypatch.setattr(module,'guard_clone_host',worker.guard_clone_host)
    monkeypatch.setattr(worker.platform,'system',lambda:'Linux')
    monkeypatch.setattr(worker,'os',SimpleNamespace(geteuid=lambda:0))
    monkeypatch.setattr(worker.platform,'node',lambda:'TOWER-test')
    with pytest.raises(BootstrapError,match='PROTECTED_HOST'):
        module.notify(sender.config)
    monkeypatch.setattr(worker.platform,'node',lambda:'XZ-31-001')
    assert module.notify(sender.config)['code'] == 'SHUTDOWN_EVENT_SENT'


def test_unit_has_group_deadline_and_no_bootstrap_ordering_cycle():
    unit = configparser.ConfigParser(interpolation=None)
    unit.read(Path(__file__).resolve().parents[2]/'deploy/bootstrap/clone/maas-worker-shutdown-notify.service')
    assert unit['Service']['TimeoutStopSec'] == '1s'
    assert unit['Service']['TimeoutStopFailureMode'] == 'kill'
    assert unit['Service']['KillMode'] == 'control-group'
    assert unit['Service']['ExecStop'].startswith('-')
    assert 'maas-worker-bootstrap.service' not in unit['Unit']['After']
    assert unit['Install']['WantedBy'] == 'multi-user.target'
