"""Bounded, late worker startup. Never changes hostname, VM state or Docker service."""
import json
import os
from pathlib import Path
import re
import signal
import subprocess
import sys
import time

from .client import BootstrapError, LocalState, atomic_json, https_opener
from .handoff import gpu_uuid
from .worker import guard_clone_host

STATE = Path('/run/maas-worker-startup')
BOUNDARY_POLICY = Path('/etc/maas/clone-boundary.json')
CODE = re.compile(r'[A-Z][A-Z0-9_]{0,95}\Z')


def command(argv, timeout):
    """Kill the whole attempt on timeout; never expose child stderr or credentials."""
    try:
        process = subprocess.Popen(argv, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                   text=True, start_new_session=True)
    except OSError:
        raise BootstrapError('BOOT_COMMAND_UNAVAILABLE') from None
    try:
        stdout, _ = process.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        process.communicate()
        raise BootstrapError('BOOT_COMMAND_TIMEOUT') from None
    return process.returncode, stdout


def remaining(deadline, limit, clock=time.monotonic):
    seconds = deadline - clock()
    if seconds <= 0:
        raise BootstrapError('BOOT_START_TIMEOUT')
    return min(seconds, limit)


def readiness(settings, *, deadline=None, runner=command, clock=time.monotonic, opener=None):
    """Read-only checks. No configuration writes, boundary changes or enrollment."""
    guard_clone_host()
    if deadline is None:
        deadline = clock() + 30

    def check(argv, reason):
        status, output = runner(argv, remaining(deadline, 5, clock))
        if status:
            raise BootstrapError(reason)
        return output.strip()

    check(['systemctl', 'is-active', '--quiet', 'multi-user.target'], 'BOOT_SYSTEM_WAITING')
    status, output = runner(['systemctl', 'show', 'swcfghostname.service',
                             '-p', 'LoadState', '-p', 'ActiveState', '-p', 'Result',
                             '-p', 'ExecMainStartTimestampMonotonic', '-p', 'ExecMainStatus'],
                            remaining(deadline, 5, clock))
    properties = dict(line.split('=', 1) for line in output.splitlines() if '=' in line)
    if properties.get('LoadState') != 'not-found':
        if (status or properties.get('LoadState') != 'loaded'
                or properties.get('ActiveState') not in ('inactive', 'active')
                or properties.get('Result') != 'success'
                or not properties.get('ExecMainStartTimestampMonotonic', '0').isdigit()
                or int(properties.get('ExecMainStartTimestampMonotonic', '0')) <= 0
                or properties.get('ExecMainStatus') != '0'):
            raise BootstrapError('BOOT_HOSTNAME_SERVICE_WAITING')
    if not check(['docker', 'info', '--format', '{{.ServerVersion}}'], 'BOOT_DOCKER_WAITING'):
        raise BootstrapError('BOOT_DOCKER_WAITING')
    devices = check(['nvidia-smi', '--query-gpu=uuid', '--format=csv,noheader'], 'BOOT_GPU_WAITING').splitlines()
    if len(devices) != 1:
        raise BootstrapError('SINGLE_GPU_REQUIRED')
    gpu = gpu_uuid(devices[0].strip())
    grant = settings.data.get('worker_start_grant_file')
    if grant and not settings.data.get('worker_start_automatic') and not Path(grant).is_file():
        raise BootstrapError('BOOT_GRANT_WAITING')
    try:
        with (opener or https_opener(settings)).open(settings.base + '/healthz',
                                                    timeout=remaining(deadline, 3, clock)) as response:
            if response.status != 200:
                raise BootstrapError('BOOT_CONFIG_SERVICE_WAITING')
    except (OSError, ValueError):
        raise BootstrapError('BOOT_CONFIG_SERVICE_WAITING') from None
    # Catch a hostname change during the checks, before any side effects.
    guard_clone_host()
    return {'code': 'BOOT_READY', 'gpu_uuid': gpu}


def child_result(argv, timeout, runner):
    status, output = runner(argv, timeout)
    try:
        result = json.loads(output)
        if not isinstance(result, dict) or not CODE.fullmatch(result.get('code', '')):
            raise ValueError()
    except (ValueError, TypeError):
        raise BootstrapError('BOOT_CHILD_RESULT_INVALID') from None
    if status:
        raise BootstrapError(result['code'])
    if result['code'] not in ('APPLIED', 'UNCHANGED'):
        raise BootstrapError('BOOT_CHILD_RESULT_INVALID')
    return result


def start(settings, *, config_path='/etc/maas/bootstrap.json', timeout=600, interval=10,
          state_dir=STATE, boundary_required=None,
          runner=command, probe=readiness, clock=time.monotonic, sleeper=time.sleep, report=None):
    if timeout <= 0 or interval <= 0:
        raise BootstrapError('BOOT_WAIT_POLICY_INVALID')
    if os.geteuid() != 0 or sys.platform != 'linux':
        raise BootstrapError('LINUX_ROOT_REQUIRED')
    if boundary_required is None:
        boundary_required = os.environ.get('MAAS_BOOTSTRAP_REQUIRE_BOUNDARY') == '1'
    # A boundary policy also makes the fence mandatory for direct manual recovery.
    boundary_required = boundary_required or BOUNDARY_POLICY.exists()
    report = report or (lambda result: print(json.dumps(result), flush=True))
    state = LocalState(state_dir)
    with state.lock():
        deadline = clock() + timeout
        attempt = 0
        reason = 'BOOT_SYSTEM_WAITING'

        def record(result):
            atomic_json(state.root / 'last-result.json', result)
            report(result)

        while clock() < deadline:
            attempt += 1
            try:
                probe(settings, deadline=deadline, runner=runner, clock=clock)
                if boundary_required:
                    status, _ = runner([sys.executable, '-m', 'maas_bootstrap.clone_boundary',
                                        'apply', '--policy', str(BOUNDARY_POLICY)],
                                       remaining(deadline, 45, clock))
                    if status:
                        raise BootstrapError('BOOT_BOUNDARY_FAILED')
                # apply rechecks the actual host, GPU and signed configuration before registration.
                result = child_result([sys.executable, '-m', 'maas_bootstrap.cli',
                                       '--config', str(config_path), 'apply', '--trigger', 'boot'],
                                      remaining(deadline, 150, clock), runner)
                record({**result, 'attempt': attempt})
                return result
            except BootstrapError as error:
                reason = error.code
                record({'code': 'BOOT_WAITING', 'reason': reason, 'attempt': attempt})
            sleeper(max(0, min(interval, deadline - clock())))
        record({'code': 'BOOT_START_TIMEOUT', 'reason': reason, 'attempt': attempt})
        raise BootstrapError('BOOT_START_TIMEOUT')
