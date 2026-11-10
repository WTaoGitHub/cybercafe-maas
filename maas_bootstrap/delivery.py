"""Operator delivery of an approved handoff to a prepared diskless clone."""

import argparse
from contextlib import contextmanager, suppress
import hashlib
import json
import os
from pathlib import Path
import select
import subprocess
import sys
import time
from uuid import UUID

from .client import BootstrapError, HTTPSConfigFetcher, LocalState, ReleaseVerifier
from .client import SAFE_REVISION, atomic_bytes, atomic_json, canonical, read_file
from .handoff import BOOT_GRANT, gpu_uuid, positive_id


GRANT_PATH = Path('/run/maas-start/boot-grant')
BOOT_ID_PATH = Path('/proc/sys/kernel/random/boot_id')
PATH_UNIT = 'maas-worker-bootstrap.path'
SCOPE_FIELDS = ('site_id', 'batch_id', 'cluster_id', 'image_id')


def grant_session(raw):
    try:
        match = BOOT_GRANT.fullmatch(raw.decode('ascii').strip())
    except UnicodeError:
        match = None
    if match is None:
        raise BootstrapError('WORKER_START_GRANT_INVALID')
    return match[1]


def existing_grant(path):
    return read_file(path, secret=True) if path.exists() or path.is_symlink() else None


def target(settings, exchange):
    from .worker import NativeDockerWorker, run

    runtime = NativeDockerWorker()
    runtime.guard()
    if settings.data.get('worker_start_grant_file') != str(GRANT_PATH):
        raise BootstrapError('DELIVERY_DISKLESS_CONFIG_REQUIRED')
    run(['systemctl', 'is-active', '--quiet', PATH_UNIT])

    state = LocalState(settings.data['state_dir'])
    with LocalState(GRANT_PATH.parent).lock():
        if GRANT_PATH.parent.resolve() != GRANT_PATH.parent:
            raise BootstrapError('DELIVERY_GRANT_DIRECTORY_UNTRUSTED')
        if run(['findmnt', '-n', '-o', 'FSTYPE', '--target', str(GRANT_PATH.parent)]) != 'tmpfs':
            raise BootstrapError('DISKLESS_TMPFS_REQUIRED')
        with state.lock():
            envelope = HTTPSConfigFetcher().fetch(settings)
            metadata = ReleaseVerifier().verify(settings, envelope)
            runtime.check_diskless_storage(settings)
            current = runtime.inspect()
            runtime.validate_layout(settings, envelope, current)
            gpu = gpu_uuid(runtime.check_environment(settings, envelope))
            binding = {key: settings.data[key] for key in SCOPE_FIELDS}
            binding.update(boot_id=str(UUID(BOOT_ID_PATH.read_text().strip())), gpu_uuid=gpu,
                           config_revision=metadata['revision'], fingerprint=metadata['fingerprint'])
            state.prepare(envelope, metadata)
            identity = state.read('identity.json')
            on_disk = existing_grant(GRANT_PATH)
            session = grant_session(on_disk) if on_disk else None
            if identity and identity.get('session_id') != session:
                raise BootstrapError('DELIVERY_SESSION_CONFLICT')
            if current and (not identity or current['session_id'] != session
                            or current['status'] not in ('created', 'running')):
                raise BootstrapError('DELIVERY_SESSION_CONFLICT')
            if not identity and any(Path(settings.data['worker_data_dir']).iterdir()):
                raise BootstrapError('DELIVERY_SESSION_CONFLICT')
        payload = exchange({'code': 'DELIVERY_TARGET_READY', 'binding': binding, 'session_id': session})
        if (not isinstance(payload, dict) or not isinstance(payload.get('binding'), dict)
                or not isinstance(payload.get('grant'), str)):
            raise BootstrapError('DELIVERY_REQUEST_INVALID')
        if payload['binding'] != binding:
            raise BootstrapError('DELIVERY_TARGET_CHANGED')
        raw = (payload['grant'] + '\n').encode('ascii')
        issued_session = grant_session(raw)
        if on_disk is not None and on_disk != raw:
            raise BootstrapError('DELIVERY_SESSION_CONFLICT')
        if on_disk is None:
            atomic_bytes(GRANT_PATH, raw)
        return {'code': 'GRANT_DELIVERED', 'session_id': issued_session}


@contextmanager
def ssh_target(host):
    argv = ['ssh', '-T', '-o', 'BatchMode=yes', '-o', 'StrictHostKeyChecking=yes',
            '-o', 'ConnectTimeout=8', '-o', 'ForwardAgent=no', '-o', 'ClearAllForwardings=yes',
            '-o', 'ControlPath=none', '--', host, '/opt/maas/bin/maas-worker-bootstrap', 'receive-grant']
    try:
        process = subprocess.Popen(argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    except OSError:
        raise BootstrapError('DELIVERY_SSH_UNCONFIRMED') from None
    try:
        yield process
    finally:
        with suppress(OSError):
            process.stdin.close()
        if process.poll() is None:
            process.kill()
        process.wait()
        process.stdout.close()


def read_reply(stream, expected_code):
    deadline = time.monotonic() + 120
    data = bytearray()
    try:
        while b'\n' not in data:
            remaining = deadline - time.monotonic()
            if remaining <= 0 or not select.select([stream], [], [], remaining)[0]:
                raise ValueError()
            chunk = os.read(stream.fileno(), 4096)
            if not chunk or len(data) + len(chunk) > 8192:
                raise ValueError()
            data.extend(chunk)
        reply = json.loads(data)
        code = reply['code']
        if not SAFE_REVISION.fullmatch(code):
            raise ValueError()
    except (OSError, ValueError, KeyError, TypeError):
        raise BootstrapError('DELIVERY_SSH_UNCONFIRMED') from None
    if code != expected_code:
        raise BootstrapError(code)
    return reply


def deliver(root, operation_dir, ssh_host, boot_id, gpu, cluster_id, config_revision,
            worker_id, previous_session_id, fence_receipt):
    from .enrollment import approve, private_root

    if not SAFE_REVISION.fullmatch(ssh_host):
        raise BootstrapError('DELIVERY_SSH_ALIAS_INVALID')
    if not (positive_id(worker_id) and positive_id(cluster_id)
            and SAFE_REVISION.fullmatch(config_revision) and 1 <= len(fence_receipt.strip()) <= 128):
        raise BootstrapError('DELIVERY_SCOPE_INVALID')
    previous_session_id = str(UUID(previous_session_id))
    expected = dict(boot_id=str(UUID(boot_id)), gpu_uuid=gpu_uuid(gpu),
                    cluster_id=cluster_id, config_revision=config_revision)
    root = private_root(root).resolve()
    op = LocalState(operation_dir)
    if op.root.resolve().is_relative_to(root):
        raise BootstrapError('DELIVERY_OPERATION_DIRECTORY_INVALID')
    # Keep the boot check and credential delivery on one verified SSH connection.
    with op.lock(), ssh_target(ssh_host) as remote:
        response = read_reply(remote.stdout, 'DELIVERY_TARGET_READY')
        binding = response['binding']
        if any(binding[k] != v for k, v in expected.items()):
            raise BootstrapError('DELIVERY_TARGET_CHANGED')
        approval = {key: binding[key] for key in (*SCOPE_FIELDS, 'config_revision')}
        approval.update(gpu=binding['gpu_uuid'], worker_id=worker_id,
                        previous_session_id=previous_session_id, fence_receipt=fence_receipt)
        intent = {'approval': approval, 'binding': binding, 'ssh_host': ssh_host, 'approval_root': str(root)}
        saved = op.read('intent.json')
        if saved is not None and saved != intent:
            raise BootstrapError('DELIVERY_OPERATION_CONFLICT')
        grant = op.root / 'boot-grant'
        raw = existing_grant(grant)
        if raw is None:
            if saved is not None or response['session_id'] is not None:
                raise BootstrapError('DELIVERY_NEEDS_RECONCILIATION')
            atomic_json(op.root / 'intent.json', intent)
            approve(root, grant, **approval)
            raw = read_file(grant, secret=True)
        elif saved is None:
            raise BootstrapError('DELIVERY_NEEDS_RECONCILIATION')
        session = grant_session(raw)
        record = LocalState(root / session).read('grant.json')
        record_fields = {key: value for key, value in approval.items() if key != 'gpu'} | {'gpu_uuid': approval['gpu']}
        if (not record or any(record.get(k) != v for k, v in record_fields.items())
                or record['token_sha256'] != hashlib.sha256(raw.strip()).hexdigest()):
            raise BootstrapError('DELIVERY_NEEDS_RECONCILIATION')
        if response['session_id'] not in (None, session):
            raise BootstrapError('DELIVERY_SESSION_CONFLICT')
        if response['session_id'] is None and record['expires_at'] <= time.time():
            raise BootstrapError('WORKER_START_GRANT_EXPIRED')
        try:
            remote.stdin.write(canonical({'binding': binding, 'grant': raw.decode().strip()}) + b'\n')
            remote.stdin.flush()
            delivered = read_reply(remote.stdout, 'GRANT_DELIVERED')
            if delivered['session_id'] != session or remote.wait(timeout=8):
                raise BootstrapError('DELIVERY_SSH_UNCONFIRMED')
        except (OSError, subprocess.TimeoutExpired):
            raise BootstrapError('DELIVERY_SSH_UNCONFIRMED') from None
        return {'code': 'GRANT_DELIVERED', 'worker_id': worker_id, 'session_id': session}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    for option in ('root', 'operation-dir', 'ssh-host', 'boot-id', 'gpu',
                   'config-revision', 'previous-session-id', 'fence-receipt'):
        parser.add_argument('--' + option, required=True)
    parser.add_argument('--cluster-id', required=True, type=int)
    parser.add_argument('--worker-id', required=True, type=int)
    try:
        result = deliver(**vars(parser.parse_args(argv)))
    except BootstrapError as error:
        print(json.dumps({'code': error.code}))
        return 1
    except (OSError, ValueError, KeyError, TypeError):
        print(json.dumps({'code': 'DELIVERY_FAILED'}))
        return 1
    print(json.dumps(result))
    return 0


if __name__ == '__main__':
    sys.exit(main())
