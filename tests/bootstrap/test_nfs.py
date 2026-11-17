"""Offline NFS checks. Mount commands are simulated; no server or VM is changed."""
import copy
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

import pytest

from maas_bootstrap import cli, nfs, storage, worker
from maas_bootstrap.client import BootstrapError, LocalSettings, atomic_json
from test_client import setup, Fetcher
from test_worker import FakeWorker

PROBE = b'host-nfs-pilot-v1\n'
CONFIG = {'schema_version': 1, 'source': '192.0.2.10:/root/maas/data/modelsWeights', 'probe_file': 'probe.txt',
          'probe_sha256': hashlib.sha256(PROBE).hexdigest()}


@pytest.fixture
def configured(setup, monkeypatch, tmp_path):
    settings, sign = setup
    data = {**settings.data, 'model_nfs': copy.deepcopy(CONFIG)}
    path = tmp_path / 'nfs-settings.json'
    atomic_json(path, data)
    settings = LocalSettings(path)
    info = tmp_path / 'mountinfo'
    info.write_text('')
    monkeypatch.setattr(nfs, 'MOUNTINFO', info)
    monkeypatch.setattr(worker, 'guard_clone_host', lambda: None)
    monkeypatch.setattr(nfs.shutil, 'which', lambda name: '/sbin/' + name)
    return settings, sign, info


def mounted_line(settings, *, source=CONFIG['source'], kind='nfs4', flags='ro,relatime', options='ro,hard,proto=tcp'):
    point = settings.data['model_root'].replace(' ', '\\040')
    return f'31 1 0:42 / {point} {flags} shared:1 - {kind} {source} {options}\n'


def automount_line(settings):
    return mounted_line(settings, source='systemd-1', kind='autofs', flags='rw,relatime', options='rw')


def unit_properties(settings, unit):
    if unit.endswith('.automount'):
        values = {'Where': settings.data['model_root'], 'ActiveState': 'active', 'Transient': 'yes'}
    else:
        values = {'What': CONFIG['source'], 'Where': settings.data['model_root'], 'Type': 'nfs',
                  'Options': nfs.OPTIONS, 'Transient': 'yes', 'ActiveState': 'inactive'}
    return '\n'.join(f'{key}={value}' for key, value in values.items())


@pytest.mark.parametrize('change', [
    None, {}, {'schema_version': True}, {'schema_version': 2}, {'options': 'rw'},
    {'source': '-o:/models'}, {'source': 'nfs;bad:/models'}, {'source': 'nfs:/../models'},
    {'source': 'nfs://models'}, {'source': 'nfs:/models\n'}, {'source': 'nfs'},
    {'probe_file': '/etc/passwd'}, {'probe_file': '../secret'}, {'probe_file': '.'},
    {'probe_file': 'a/../probe.txt'}, {'probe_file': 'a//probe.txt'},
    {'probe_file': False}, {'probe_sha256': 'bad'}, {'probe_sha256': False},
])
def test_local_nfs_policy_rejects_unknown_or_unsafe_values(setup, tmp_path, change):
    settings, _ = setup
    cfg = change if change in (None, {}) else {**CONFIG, **change}
    path = tmp_path / 'bad-nfs.json'
    atomic_json(path, {**settings.data, 'model_nfs': cfg})
    with pytest.raises(BootstrapError, match='LOCAL_CONFIG_INVALID'):
        LocalSettings(path)


@pytest.mark.parametrize('source', ['nfs.example.invalid:/releases', '[2001:db8::1]:/models'])
def test_explicit_dns_and_ipv6_sources_are_accepted(source):
    cfg = {**CONFIG, 'source': source}
    assert nfs.configuration({'model_nfs': cfg}) == cfg


def test_signed_release_must_match_local_nfs_policy(configured):
    settings, sign, _ = configured
    manifest = {**sign()['manifest'], 'model_nfs': CONFIG}
    assert worker.ReleaseVerifier().verify(settings, sign(manifest))['revision'] == 'r1'
    for changed in [sign()['manifest'], {**manifest, 'model_nfs': {**CONFIG, 'source': 'other:/models'}}]:
        with pytest.raises(BootstrapError, match='NFS_CONFIG_MISMATCH'):
            worker.ReleaseVerifier().verify(settings, sign(changed))
    del settings.data['model_nfs']
    with pytest.raises(BootstrapError, match='NFS_CONFIG_MISMATCH'):
        worker.ReleaseVerifier().verify(settings, sign(manifest))


@pytest.mark.parametrize('change', [
    {'source': 'wrong:/models'}, {'kind': 'ext4'}, {'flags': 'rw,relatime'},
    {'options': 'ro,soft,proto=tcp'}, {'options': 'ro,hard,proto=udp'},
    {'options': 'ro,hard,softreval,proto=tcp'},
])
def test_wrong_mount_is_never_replaced_or_accepted(configured, monkeypatch, change):
    settings, _, info = configured
    info.write_text(mounted_line(settings, **change))
    monkeypatch.setattr(nfs, 'run', lambda *a: pytest.fail('Existing mount must not be changed'))
    with pytest.raises(BootstrapError, match='NFS_MOUNT_MISMATCH'):
        storage.prepare(settings)


def test_stacked_or_nested_mounts_are_rejected(configured):
    settings, _, info = configured
    line = mounted_line(settings)
    for text in [line + line, line + line.replace(settings.data['model_root'], settings.data['model_root'] + '/nested')]:
        info.write_text(text)
        with pytest.raises(BootstrapError, match='NFS_MOUNT_CONFLICT'):
            nfs.mounted(Path(settings.data['model_root']), CONFIG['source'])


def test_empty_local_directory_is_not_nfs_and_status_does_not_mount(configured, monkeypatch):
    settings, _, _ = configured
    monkeypatch.setattr(nfs, 'run', lambda *a: pytest.fail('Status cannot mount'))
    with pytest.raises(BootstrapError, match='NFS_AUTOMOUNT_NOT_READY'):
        storage.status(settings)
    assert not Path(settings.data['model_root']).exists()


def test_lazy_preparation_is_repeatable_and_never_connects_or_reads_remote_files(configured, monkeypatch):
    settings, _, info = configured
    root = Path(settings.data['model_root'])
    mounts = []

    def command(settings, args, failure):
        if args[0] == 'systemd-escape':
            return 'srv-maas-models.mount'
        elif args[0] == 'systemd-mount':
            assert '--automount=yes' in args and '--options=' + nfs.OPTIONS in args
            assert args[-2:] == [CONFIG['source'], str(root)]
            mounts.append(args)
            info.write_text(automount_line(settings))
        elif args[0] == 'systemctl':
            return unit_properties(settings, args[2])
        elif args[3] == 'target':
            nfs.check_target(root)
        else:
            pytest.fail('Boot must not trigger the NFS mount or read a remote file')

    monkeypatch.setattr(nfs, 'run', command)
    first = storage.prepare(settings)
    assert first == storage.prepare(settings) == storage.status(settings)
    assert first['nfs_state'] == 'automount' and not first['probe_verified']
    assert len(mounts) == 1
    assert not (root / CONFIG['probe_file']).exists()
    assert not (root / 'image-contained').exists()
    assert not Path(settings.data['worker_data_dir']).exists()
    assert Path(settings.data['model_cache_dir']).is_dir()


def test_mount_exit_zero_without_real_mount_cannot_succeed(configured, monkeypatch):
    settings, _, _ = configured
    monkeypatch.setattr(nfs, 'run', lambda s, args, failure: 'srv-maas-models.mount' if args[0] == 'systemd-escape' else '')
    with pytest.raises(BootstrapError, match='NFS_AUTOMOUNT_NOT_READY'):
        storage.prepare(settings)


def test_explicit_verification_triggers_access_and_then_requires_actual_nfs(configured, monkeypatch):
    settings, _, info = configured
    root = Path(settings.data['model_root']); root.mkdir()
    info.write_text(automount_line(settings))
    probes = []
    def command(settings, args, failure):
        if args[0] == 'systemd-escape': return 'srv-maas-models.mount'
        if args[0] == 'systemctl': return unit_properties(settings, args[2])
        assert args[3] == 'probe'
        probes.append(args)
        info.write_text(automount_line(settings) + mounted_line(settings))
        (root / CONFIG['probe_file']).write_bytes(PROBE)
        nfs.check_probe(root, CONFIG['source'], CONFIG['probe_file'], CONFIG['probe_sha256'])
    monkeypatch.setattr(nfs, 'run', command)
    assert nfs.verify(settings) == {'nfs_state': 'mounted', 'probe_verified': True}
    assert len(probes) == 1


def test_foreign_automount_configuration_cannot_be_reported_ready(configured, monkeypatch):
    settings, _, info = configured
    info.write_text(automount_line(settings))
    def command(settings, args, failure):
        if args[0] == 'systemd-escape': return 'srv-maas-models.mount'
        return unit_properties(settings, args[2]).replace(CONFIG['source'], 'wrong:/export')
    monkeypatch.setattr(nfs, 'run', command)
    with pytest.raises(BootstrapError, match='NFS_UNIT_MISMATCH'):
        storage.prepare(settings)


def test_missing_nfs_client_fails_before_preparing_units(configured, monkeypatch):
    settings, _, _ = configured
    monkeypatch.setattr(nfs.shutil, 'which', lambda name: None)
    monkeypatch.setattr(nfs, 'run', lambda *a: pytest.fail('Client is missing'))
    with pytest.raises(BootstrapError, match='NFS_CLIENT_UNAVAILABLE'):
        storage.prepare(settings)


def test_failed_first_access_is_reported_without_reset_or_retry(configured, monkeypatch):
    settings, _, info = configured
    info.write_text(automount_line(settings))
    def command(settings, args, failure):
        if args[0] == 'systemd-escape': return 'srv-maas-models.mount'
        return unit_properties(settings, args[2]).replace('ActiveState=inactive', 'ActiveState=failed')
    monkeypatch.setattr(nfs, 'run', command)
    with pytest.raises(BootstrapError, match='NFS_MOUNT_FAILED'):
        storage.prepare(settings)


def test_target_check_preserves_local_files_and_rejects_symlinks(tmp_path):
    root = tmp_path / 'models'
    root.mkdir()
    (root / 'existing').write_text('keep')
    with pytest.raises(BootstrapError, match='NFS_TARGET_NOT_EMPTY'):
        nfs.check_target(root)
    assert (root / 'existing').read_text() == 'keep'
    link = tmp_path / 'link'; link.symlink_to(root, target_is_directory=True)
    with pytest.raises(BootstrapError, match='MODEL_STORAGE_UNTRUSTED'):
        nfs.check_target(link)


@pytest.mark.parametrize('kind', ['missing', 'hash', 'symlink', 'fifo', 'oversize'])
def test_probe_checks_content_and_refuses_special_files(configured, kind):
    settings, _, info = configured
    root = Path(settings.data['model_root']); root.mkdir()
    info.write_text(mounted_line(settings))
    probe = root / CONFIG['probe_file']
    if kind == 'hash': probe.write_bytes(b'wrong revision')
    elif kind == 'symlink': probe.symlink_to(root / 'other')
    elif kind == 'fifo': os.mkfifo(probe)
    elif kind == 'oversize': probe.write_bytes(b'x' * (nfs.MAX_BYTES + 1))
    with pytest.raises((BootstrapError, OSError)):
        nfs.check_probe(root, CONFIG['source'], CONFIG['probe_file'], CONFIG['probe_sha256'])


def test_nfs_root_is_not_resolved_in_parent_process(configured, monkeypatch):
    settings, _, _ = configured
    original = Path.resolve
    def resolve(path, *args, **kwargs):
        if path == Path(settings.data['model_root']):
            pytest.fail('Remote metadata access must be isolated')
        return original(path, *args, **kwargs)
    monkeypatch.setattr(Path, 'resolve', resolve)
    storage.paths(settings.data)


def test_timeout_leaves_fence_and_never_waits_unbounded(configured, monkeypatch):
    settings, _, _ = configured
    waits, kills = [], []
    class Process:
        pid = 12345
        def communicate(self, timeout):
            waits.append(timeout)
            raise subprocess.TimeoutExpired('simulated-stalled-nfs', timeout)
    monkeypatch.setattr(nfs.subprocess, 'Popen', lambda *a, **k: Process())
    monkeypatch.setattr(nfs.os, 'killpg', lambda *args: kills.append(args))
    with pytest.raises(BootstrapError, match='NFS_OPERATION_TIMEOUT'):
        nfs.run(settings, ['mount'], 'NFS_MOUNT_FAILED')
    assert waits == [nfs.OPERATION_TIMEOUT, 1] and kills == [(12345, nfs.signal.SIGKILL)]
    fence = Path(settings.data['state_dir']) / 'nfs-operation.json'
    assert json.loads(fence.read_text())['code'] == 'NFS_OPERATION_TIMEOUT'
    assert fence.stat().st_mode & 0o777 == 0o600
    monkeypatch.setattr(nfs.subprocess, 'Popen', lambda *a, **k: pytest.fail('Fenced operation must not restart'))
    with pytest.raises(BootstrapError, match='NFS_OPERATION_PENDING'):
        storage.prepare(settings)


def test_real_child_failure_is_bounded_and_does_not_keep_pending_fence(configured):
    settings, _, _ = configured
    with pytest.raises(BootstrapError, match='NFS_PROBE_FAILED'):
        nfs.run(settings, [sys.executable, '-c', 'raise SystemExit(1)'], 'NFS_PROBE_FAILED')
    assert not (Path(settings.data['state_dir']) / 'nfs-operation.json').exists()


def test_apply_checks_nfs_before_environment_identity_or_permit(configured, monkeypatch):
    settings, sign, _ = configured
    runtime = FakeWorker()
    def fail(*args): raise BootstrapError('NFS_NOT_MOUNTED')
    monkeypatch.setattr(storage, 'prepare', fail)
    runtime.check_environment = lambda *a: pytest.fail('NFS must be ready first')
    manifest = {**sign()['manifest'], 'model_nfs': CONFIG}
    permit = SimpleNamespace(fetch=lambda *a: pytest.fail('No short-lived permit before storage'))
    with pytest.raises(BootstrapError, match='NFS_NOT_MOUNTED'):
        worker.apply(settings, fetcher=Fetcher(sign(manifest)), backend=runtime, permit_fetcher=permit)
    assert runtime.creates == runtime.starts == 0
    assert not (Path(settings.data['state_dir']) / 'identity.json').exists()


def test_storage_prepare_cli_verifies_signature_and_never_enrolls(configured, monkeypatch, capsys):
    settings, sign, _ = configured
    manifest = {**sign()['manifest'], 'model_nfs': CONFIG}
    from maas_bootstrap import client
    monkeypatch.setattr(client, 'HTTPSConfigFetcher', lambda: Fetcher(sign(manifest)))
    monkeypatch.setattr(storage, 'prepare', lambda s: {'storage_mode': 'nfs'})
    monkeypatch.setattr(worker, 'apply', lambda *a, **k: pytest.fail('Host-only command cannot enroll'))
    config = Path(settings.data['state_dir']).parent / 'nfs-settings.json'
    assert cli.main(['--config', str(config), 'storage-prepare']) == 0
    assert json.loads(capsys.readouterr().out)['code'] == 'MODEL_STORAGE_PREPARED'
    monkeypatch.setattr(client, 'HTTPSConfigFetcher', lambda: Fetcher(sign()))
    monkeypatch.setattr(storage, 'prepare', lambda s: pytest.fail('Unsigned NFS cannot mount'))
    assert cli.main(['--config', str(config), 'storage-prepare']) == 1
    assert json.loads(capsys.readouterr().out)['code'] == 'NFS_CONFIG_MISMATCH'
