"""Late startup retries do not register until prerequisites and the fence succeed."""
import json
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

import pytest

from maas_bootstrap import startup as s
from maas_bootstrap.client import BootstrapError, LocalState

GPU = 'GPU-12345678-1234-1234-1234-123456789abc'
SETTINGS = SimpleNamespace(data={}, base='https://bootstrap.example.invalid')


class Clock:
    now = 0

    def __call__(self):
        return self.now

    def sleep(self, seconds):
        self.now += seconds


@pytest.fixture
def boot(monkeypatch, tmp_path):
    monkeypatch.setattr(s, 'os', SimpleNamespace(geteuid=lambda: 0, environ={}))
    monkeypatch.setattr(s, 'sys', SimpleNamespace(platform='linux', executable=sys.executable))
    monkeypatch.setattr(s, 'BOUNDARY_POLICY', tmp_path / 'absent-policy.json')
    clock = Clock()
    reports = []
    return dict(state_dir=tmp_path / 'startup', clock=clock, sleeper=clock.sleep,
                report=reports.append), reports


def ready(*args, **kwargs):
    return {'code': 'BOOT_READY', 'gpu_uuid': GPU}


def test_waiting_hostname_then_automatically_runs_boundary_before_apply(boot):
    kwargs, reports = boot
    probes = []
    commands = []

    def probe(*args, **kw):
        probes.append(1)
        if len(probes) < 3:
            raise BootstrapError('PROTECTED_HOST_WORKER_FORBIDDEN')
        return ready()

    def runner(argv, timeout):
        commands.append(argv)
        return (0, '{}') if 'maas_bootstrap.clone_boundary' in argv else (0, '{"code":"APPLIED"}')

    result = s.start(SETTINGS, boundary_required=True, probe=probe, runner=runner, **kwargs)
    assert result['code'] == 'APPLIED' and kwargs['clock'].now == 20
    assert len(commands) == 2 and 'maas_bootstrap.clone_boundary' in commands[0]
    assert commands[1][-3:] == ['apply', '--trigger', 'boot']
    assert reports[-1]['attempt'] == 3


def test_timeout_does_not_execute_boundary_or_registration_and_records_reason(boot):
    kwargs, reports = boot

    def probe(*a, **k):
        raise BootstrapError('BOOT_DOCKER_WAITING')

    with pytest.raises(BootstrapError, match='BOOT_START_TIMEOUT'):
        s.start(SETTINGS, timeout=25, probe=probe,
                runner=lambda *a: pytest.fail('Not ready; command must not run'), **kwargs)
    assert kwargs['clock'].now == 25
    assert reports[-1] == {'code': 'BOOT_START_TIMEOUT', 'reason': 'BOOT_DOCKER_WAITING', 'attempt': 3}
    assert json.loads((kwargs['state_dir'] / 'last-result.json').read_text()) == reports[-1]


def test_failed_boundary_is_retried_and_cannot_start_worker_first(boot):
    kwargs, _ = boot
    phases = []

    def runner(argv, timeout):
        boundary = 'maas_bootstrap.clone_boundary' in argv
        phases.append('boundary' if boundary else 'apply')
        if boundary:
            return (1 if len(phases) == 1 else 0), '{}'
        return 0, '{"code":"APPLIED"}'

    s.start(SETTINGS, boundary_required=True, probe=ready, runner=runner, **kwargs)
    assert phases == ['boundary', 'boundary', 'apply']


def test_policy_file_requires_boundary_even_without_systemd_environment(boot, monkeypatch, tmp_path):
    kwargs, _ = boot
    policy = tmp_path / 'policy.json'; policy.write_text('{}')
    monkeypatch.setattr(s, 'BOUNDARY_POLICY', policy)
    commands = []

    def runner(argv, timeout):
        commands.append(argv)
        return 0, '{"code":"UNCHANGED"}'

    s.start(SETTINGS, probe=ready, runner=runner, **kwargs)
    assert 'maas_bootstrap.clone_boundary' in commands[0]


def test_deadline_limits_attempt_and_does_not_start_worker_after_boundary_timeout(boot):
    kwargs, reports = boot

    def runner(argv, timeout):
        assert 'maas_bootstrap.clone_boundary' in argv and timeout == 20
        kwargs['clock'].now += timeout
        raise BootstrapError('BOOT_COMMAND_TIMEOUT')

    with pytest.raises(BootstrapError, match='BOOT_START_TIMEOUT'):
        s.start(SETTINGS, timeout=20, boundary_required=True, probe=ready, runner=runner, **kwargs)
    assert kwargs['clock'].now == 20 and reports[-1]['reason'] == 'BOOT_COMMAND_TIMEOUT'


def test_child_failure_logs_only_code_and_retries_existing_apply(boot):
    kwargs, reports = boot
    count = []

    def runner(argv, timeout):
        count.append(1)
        if len(count) == 1:
            return 1, '{"code":"REGISTRATION_UNCONFIRMED","token":"never-log-this"}'
        return 0, '{"code":"UNCHANGED"}'

    assert s.start(SETTINGS, probe=ready, runner=runner, **kwargs)['code'] == 'UNCHANGED'
    assert len(count) == 2 and 'never-log-this' not in json.dumps(reports)


def test_concurrent_start_does_not_launch_second_attempt(boot):
    kwargs, _ = boot
    with LocalState(kwargs['state_dir']).lock(), pytest.raises(BootstrapError, match='BOOTSTRAP_BUSY'):
        s.start(SETTINGS, probe=lambda *a, **k: pytest.fail('Concurrent probe'), **kwargs)


@pytest.mark.parametrize('properties', [
    'LoadState=loaded\nActiveState=activating\nResult=success\nExecMainStartTimestampMonotonic=1\nExecMainStatus=0',
    'LoadState=loaded\nActiveState=failed\nResult=exit-code\nExecMainStartTimestampMonotonic=1\nExecMainStatus=1',
    'LoadState=loaded\nActiveState=inactive\nResult=success\nExecMainStartTimestampMonotonic=0\nExecMainStatus=0',
])
def test_hostname_unit_must_have_completed_before_docker_probe(monkeypatch, properties):
    monkeypatch.setattr(s, 'guard_clone_host', lambda: None)

    def runner(argv, timeout):
        assert argv[0] == 'systemctl'
        return 0, properties if argv[1] == 'show' else ''

    with pytest.raises(BootstrapError, match='BOOT_HOSTNAME_SERVICE_WAITING'):
        s.readiness(SETTINGS, runner=runner)


def test_readiness_uses_basic_target_without_requiring_globally_healthy_system(monkeypatch):
    monkeypatch.setattr(s, 'guard_clone_host', lambda: None)
    calls = []

    def runner(argv, timeout):
        calls.append(argv)
        if argv[0] == 'docker': return 0, '28.0.0'
        if argv[0] == 'nvidia-smi': return 0, GPU
        if argv[1] == 'show':
            return 0, ('LoadState=loaded\nActiveState=inactive\nResult=success\n'
                       'ExecMainStartTimestampMonotonic=123\nExecMainStatus=0')
        return 0, ''

    class Response:
        status = 200
        def __enter__(self): return self
        def __exit__(self, *a): pass

    class Opener:
        def open(self, url, timeout):
            assert url == SETTINGS.base + '/healthz' and 0 < timeout <= 3
            return Response()

    assert s.readiness(SETTINGS, runner=runner, opener=Opener())['gpu_uuid'] == GPU
    assert ['systemctl', 'is-active', '--quiet', 'multi-user.target'] in calls
    assert all('is-system-running' not in argv for argv in calls)


def test_hostname_guard_runs_before_any_external_check(monkeypatch):
    def guard(): raise BootstrapError('PROTECTED_HOST_WORKER_FORBIDDEN')
    monkeypatch.setattr(s, 'guard_clone_host', guard)
    with pytest.raises(BootstrapError, match='PROTECTED_HOST_WORKER_FORBIDDEN'):
        s.readiness(SETTINGS, runner=lambda *a: pytest.fail('Protected host reached command'))


def test_command_timeout_kills_attempt_process_group(monkeypatch):
    calls = []

    class Process:
        pid = 12345
        count = 0
        def communicate(self, timeout=None):
            self.count += 1
            if self.count == 1: raise subprocess.TimeoutExpired('test', timeout)
            return '', None

    monkeypatch.setattr(s.subprocess, 'Popen', lambda *a, **k: Process())
    monkeypatch.setattr(s.os, 'killpg', lambda *args: calls.append(args))
    with pytest.raises(BootstrapError, match='BOOT_COMMAND_TIMEOUT'):
        s.command(['test'], .01)
    assert calls == [(12345, s.signal.SIGKILL)]
