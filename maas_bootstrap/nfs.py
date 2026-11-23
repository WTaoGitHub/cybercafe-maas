"""Host-only NFS preparation; never configures containers or changes model sources."""
import hashlib
import ipaddress
import json
import os
from pathlib import Path, PurePosixPath
import re
import signal
import shutil
import stat
import subprocess
import sys

from .client import BootstrapError, MAX_BYTES, atomic_json

MOUNTINFO = Path('/proc/self/mountinfo')
OPTIONS = 'ro,hard,proto=tcp,fg,retry=0,nosuid,nodev'
OPERATION_TIMEOUT = 20


def configuration(data):
    if 'model_nfs' not in data:
        return None
    cfg = data['model_nfs']
    if (not isinstance(cfg, dict) or set(cfg) != {'schema_version', 'source', 'probe_file', 'probe_sha256'}
            or type(cfg['schema_version']) is not int or cfg['schema_version'] != 1):
        raise ValueError('Invalid model_nfs configuration')
    source = cfg['source']
    if not isinstance(source, str) or len(source) > 1024 or ':/' not in source:
        raise ValueError('Invalid NFS source')
    host, export = source.split(':/', 1)
    if host.startswith('[') and host.endswith(']'):
        ipaddress.IPv6Address(host[1:-1])
    elif not re.fullmatch(r'[A-Za-z0-9](?:[A-Za-z0-9.-]{0,251}[A-Za-z0-9])?', host):
        raise ValueError('Invalid NFS host')
    for path, absolute in [('/' + export, True), (cfg['probe_file'], False)]:
        if (not isinstance(path, str) or not path or len(path) > 1024
                or any(c not in 'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._-/' for c in path)
                or PurePosixPath(path).is_absolute() != absolute
                or path.startswith('//') or str(PurePosixPath(path)) != path or '..' in PurePosixPath(path).parts
                or (not absolute and path == '.')):
            raise ValueError('Invalid NFS path')
    if not isinstance(cfg['probe_sha256'], str) or not re.fullmatch(r'[0-9a-f]{64}', cfg['probe_sha256']):
        raise ValueError('Invalid NFS probe digest')
    return cfg


def validate_release(settings, manifest=None):
    local = configuration(settings.data)
    published = configuration(manifest) if manifest is not None else None
    if settings.data.get('model_nfs_from_release'):
        if published is None:
            raise BootstrapError('NFS_CONFIG_REQUIRED')
        return published
    if manifest is not None and local != published:
        raise BootstrapError('NFS_CONFIG_MISMATCH')
    return local


def mounted(target, source):
    """Read kernel metadata without stat() on a potentially stalled NFS mount."""
    rows = []
    try:
        for line in MOUNTINFO.read_text().splitlines():
            before, after = line.split(' - ', 1)
            left, right = before.split(), after.split()
            point = re.sub(r'\\([0-7]{3})', lambda m: chr(int(m[1], 8)), left[4])
            if point == str(target):
                rows.append((right[0], right[1], left[5].split(','), right[2].split(',')))
            elif target in Path(point).parents:
                raise BootstrapError('NFS_MOUNT_CONFLICT')
    except (OSError, ValueError, IndexError):
        raise BootstrapError('NFS_MOUNTINFO_UNAVAILABLE') from None
    automounts = [row for row in rows if row[0] == 'autofs' and row[1] == 'systemd-1']
    filesystems = [row for row in rows if row not in automounts]
    if len(automounts) > 1 or len(filesystems) > 1:
        raise BootstrapError('NFS_MOUNT_CONFLICT')
    if not filesystems:
        return 'automount' if automounts else 'absent'
    kind, actual, flags, options = filesystems[0]
    if (kind not in ('nfs', 'nfs4') or actual != source or 'ro' not in flags
            or 'rw' in flags or 'hard' not in options or 'proto=tcp' not in options
            or any(option in options for option in ('soft', 'softerr', 'softreval'))):
        raise BootstrapError('NFS_MOUNT_MISMATCH')
    return 'mounted'


def run(settings, args, failure):
    root = Path(settings.data['state_dir'])
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    info = root.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid not in (0, os.geteuid()) or info.st_mode & 0o077:
        raise BootstrapError('STATE_DIRECTORY_UNTRUSTED')
    fence = root / 'nfs-operation.json'
    try:
        fd = os.open(fence, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    except FileExistsError:
        raise BootstrapError('NFS_OPERATION_PENDING') from None
    with os.fdopen(fd, 'w') as output:
        json.dump({'code': 'NFS_OPERATION_PENDING', 'operation': failure}, output)
        output.flush()
        os.fsync(output.fileno())
    try:
        process = subprocess.Popen(args, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                   stderr=subprocess.DEVNULL, text=True, start_new_session=True)
    except OSError:
        fence.unlink()
        raise BootstrapError('NFS_COMMAND_UNAVAILABLE') from None
    atomic_json(fence, {'code': 'NFS_OPERATION_PENDING', 'operation': failure, 'pid': process.pid})
    try:
        output, _ = process.communicate(timeout=OPERATION_TIMEOUT)
    except subprocess.TimeoutExpired:
        # Keep the fence even after SIGKILL: a timeout does not prove kernel I/O has finished.
        atomic_json(fence, {'code': 'NFS_OPERATION_TIMEOUT', 'operation': failure, 'pid': process.pid})
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        try:
            process.communicate(timeout=1)
        except subprocess.TimeoutExpired:
            pass
        raise BootstrapError('NFS_OPERATION_TIMEOUT') from None
    fence.unlink()
    if process.returncode:
        raise BootstrapError(failure)
    return output.strip()


def prepare(settings, cfg, *, create=False):
    if cfg is None:
        raise BootstrapError('NFS_NOT_CONFIGURED')
    from .worker import guard_clone_host
    guard_clone_host()
    if not shutil.which('mount.nfs'):
        raise BootstrapError('NFS_CLIENT_UNAVAILABLE')
    target = Path(settings.data['model_root'])
    if (Path(settings.data['state_dir']) / 'nfs-operation.json').exists():
        raise BootstrapError('NFS_OPERATION_PENDING')
    state = mounted(target, cfg['source'])
    if state == 'absent' and not create:
        raise BootstrapError('NFS_AUTOMOUNT_NOT_READY')
    if state != 'mounted':
        unit = run(settings, ['systemd-escape', '--path', '--suffix=mount', str(target)], 'NFS_UNIT_NAME_FAILED')
        if not re.fullmatch(r'[A-Za-z0-9_.@:\\-]+\.mount', unit):
            raise BootstrapError('NFS_UNIT_NAME_FAILED')
        auto = unit.removesuffix('.mount') + '.automount'
    if state == 'absent':
        run(settings, [sys.executable, '-m', 'maas_bootstrap.nfs', 'target', str(target)], 'NFS_TARGET_NOT_READY')
        run(settings, ['systemd-mount', '--automount=yes', '--type=nfs', '--options=' + OPTIONS,
                      '--timeout-idle-sec=infinity', '--property=TimeoutSec=20s',
                      '--property=StartLimitIntervalSec=infinity', '--property=StartLimitBurst=1',
                      '--no-ask-password', '--quiet', cfg['source'], str(target)], 'NFS_AUTOMOUNT_FAILED')
        state = mounted(target, cfg['source'])
        if state != 'automount':
            raise BootstrapError('NFS_AUTOMOUNT_NOT_READY')
    if state == 'automount':
        for name, expected in [(unit, {'What': cfg['source'], 'Where': str(target), 'Type': 'nfs',
                                      'Options': OPTIONS, 'Transient': 'yes'}),
                               (auto, {'Where': str(target), 'ActiveState': 'active', 'Transient': 'yes'})]:
            text = run(settings, ['systemctl', 'show', name, '--property=' + ','.join([*expected, 'ActiveState'])], 'NFS_UNIT_CHECK_FAILED')
            actual = dict(line.split('=', 1) for line in text.splitlines() if '=' in line)
            if actual.get('ActiveState') == 'failed':
                raise BootstrapError('NFS_MOUNT_FAILED')
            if any(actual.get(key) != value for key, value in expected.items()):
                raise BootstrapError('NFS_UNIT_MISMATCH')
    return {'storage_mode': 'nfs', 'nfs_source': cfg['source'], 'nfs_state': state, 'probe_verified': False}


def verify(settings, *, manifest=None):
    cfg = validate_release(settings, manifest)
    prepare(settings, cfg)
    target = Path(settings.data['model_root'])
    run(settings, [sys.executable, '-m', 'maas_bootstrap.nfs', 'probe', str(target),
                   cfg['source'], cfg['probe_file'], cfg['probe_sha256']], 'NFS_PROBE_FAILED')
    if mounted(target, cfg['source']) != 'mounted':
        raise BootstrapError('NFS_NOT_MOUNTED')
    return {'nfs_state': 'mounted', 'probe_verified': True}


def check_target(target):
    from .storage import directory
    directory(target, create=True)
    with os.scandir(target) as entries:
        if next(entries, None) is not None:
            raise BootstrapError('NFS_TARGET_NOT_EMPTY')


def check_probe(target, source, relative, digest):
    if mounted(target, source) == 'absent':
        raise BootstrapError('NFS_NOT_MOUNTED')
    probe = target / relative
    if target.resolve() != target or probe.resolve() != probe:
        raise BootstrapError('NFS_PROBE_UNTRUSTED')
    if mounted(target, source) != 'mounted':
        raise BootstrapError('NFS_NOT_MOUNTED')
    with os.scandir(target) as entries:
        next(entries, None)
    fd = os.open(probe, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, 'rb') as stream:
        if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
            raise BootstrapError('NFS_PROBE_UNTRUSTED')
        raw = stream.read(MAX_BYTES + 1)
    if len(raw) > MAX_BYTES or hashlib.sha256(raw).hexdigest() != digest:
        raise BootstrapError('NFS_PROBE_MISMATCH')


if __name__ == '__main__':
    try:
        if len(sys.argv) == 3 and sys.argv[1] == 'target':
            check_target(Path(sys.argv[2]))
        elif len(sys.argv) == 6 and sys.argv[1] == 'probe':
            check_probe(Path(sys.argv[2]), *sys.argv[3:])
        else:
            raise ValueError('Invalid check')
    except (BootstrapError, OSError, ValueError):
        sys.exit(1)
