"""Verified worker runtime policy and per-GPU execution credential delivery."""
import hmac
import json
import os
import re
import ssl
import stat
from pathlib import Path
from secrets import token_hex
from urllib.parse import urlsplit
from uuid import UUID

from .client import BootstrapError, DISKLESS_POLICY, LocalState, MAX_BYTES, SAFE_REVISION
from .client import atomic_bytes, atomic_json, canonical, read_file


KEY = re.compile(r'[a-f0-9]{64}')
DIGEST = re.compile(r'sha256:[a-f0-9]{64}')
PREFIX = re.compile(r'MAAS_[A-Z0-9_]{1,48}_EXECUTION_')
CERTIFICATES = re.compile(r'(?:-----BEGIN CERTIFICATE-----\r?\n'
                          r'(?:[a-zA-Z0-9+/=]+\r?\n)+-----END CERTIFICATE-----\s*){1,8}')
SERVING_DIRECTORY = '/run/maas-execution'
RECEIPT_TARGET = '/run/maas-execution-receipts'
RUNTIME_SOCKET_DIRECTORY = '/run/maas-pp-runtime'
KEY_NAME = 'worker.key'
CA_NAME = 'authority.pem'
KEYS_FILENAME = 'worker-keys.json'


def configuration(manifest):
    if 'worker_runtime' not in manifest:
        return None
    config = manifest['worker_runtime']
    try:
        if not isinstance(config, dict) or set(config) - {'profiles', 'images', 'execution'}:
            raise ValueError()
        profiles, images = config['profiles'], config['images']
        if not isinstance(profiles, dict) or len(profiles) > 128:
            raise ValueError()
        for name, profile in profiles.items():
            if (not SAFE_REVISION.fullmatch(name) or not isinstance(profile, dict)
                    or set(profile) != {'cpu_millis', 'memory_mib'}
                    or type(profile['cpu_millis']) is not int or not 1 <= profile['cpu_millis'] <= 256_000
                    or type(profile['memory_mib']) is not int or not 64 <= profile['memory_mib'] <= 1_048_576):
                raise ValueError()
        if not isinstance(images, list) or not 1 <= len(images) <= 512:
            raise ValueError()
        matches = set()
        routes = set()
        for image in images:
            if (not isinstance(image, dict) or set(image) - {'digest', 'entrypoint', 'profile', 'execution_env_prefix', 'receipt_uid', 'runtime_witness'}
                    or not DIGEST.fullmatch(image['digest'])):
                raise ValueError()
            entrypoint = image['entrypoint']
            if (not isinstance(entrypoint, list) or not 1 <= len(entrypoint) <= 16
                    or any(not isinstance(arg, str) or not 1 <= len(arg) <= 256 or not arg.isprintable()
                           for arg in entrypoint)):
                raise ValueError()
            match = (image['digest'], tuple(entrypoint))
            if match in matches or not (image.keys() & {'profile', 'execution_env_prefix'}):
                raise ValueError()
            matches.add(match)
            if 'profile' in image and image['profile'] not in profiles:
                raise ValueError()
            if 'execution_env_prefix' in image and not PREFIX.fullmatch(image['execution_env_prefix']):
                raise ValueError()
            if 'runtime_witness' in image:
                witness = image['runtime_witness']
                if witness is True:
                    # Older PP releases provisioned their route map independently.
                    if image.get('execution_env_prefix') != 'MAAS_PP_OCR_EXECUTION_' or entrypoint != ['python3', '/app/server.py']:
                        raise ValueError()
                elif (not isinstance(witness, dict) or set(witness) != {'routes'}
                      or 'receipt_uid' not in image or not isinstance(witness['routes'], list)
                      or not 1 <= len(witness['routes']) <= 128):
                    raise ValueError()
                else:
                    for name in witness['routes']:
                        if not isinstance(name, str) or not 1 <= len(name) <= 128 or not name.isprintable() or name in routes:
                            raise ValueError()
                        routes.add(name)
            if 'receipt_uid' in image:
                uid = image['receipt_uid']
                if 'execution_env_prefix' not in image or type(uid) is not int or not 0 <= uid < 2**32 - 1:
                    raise ValueError()
        requires_execution = any('execution_env_prefix' in image for image in images)
        if requires_execution != ('execution' in config):
            raise ValueError()
        if requires_execution:
            authority = config['execution']
            if (manifest['worker_name_policy'] != DISKLESS_POLICY or not isinstance(authority, dict)
                    or set(authority) != {'origin', 'ca_pem'}):
                raise ValueError()
            origin, pem = authority['origin'], authority['ca_pem']
            url = urlsplit(origin)
            if (url.scheme != 'https' or not url.hostname or url.username is not None or url.password is not None
                    or url.path or url.query or url.fragment or len(origin) > 255
                    or not origin.isascii() or not origin.isprintable() or any(ch.isspace() for ch in origin)
                    or url.port == 0 or not isinstance(pem, str) or len(pem) > 65536 or not CERTIFICATES.fullmatch(pem)):
                raise ValueError()
            ssl.create_default_context(cadata=pem)
    except (KeyError, TypeError, ValueError, AttributeError, ssl.SSLError):
        raise BootstrapError('WORKER_RUNTIME_POLICY_INVALID') from None
    return config


def witness_routes(runtime):
    return {name: {k: image[k] for k in ('digest', 'execution_env_prefix', 'entrypoint')}
            for image in runtime['images'] if isinstance(image.get('runtime_witness'), dict)
            for name in image['runtime_witness']['routes']}


def local_directory(settings):
    directory = Path(settings.data['state_dir']) / 'execution'
    native = Path(settings.data['worker_data_dir'])
    if directory.resolve() != directory or directory == native or directory in native.parents or native in directory.parents:
        raise BootstrapError('EXECUTION_DIRECTORY_UNTRUSTED')
    if directory.exists():
        mode = directory.lstat()
        if not stat.S_ISDIR(mode.st_mode) or mode.st_mode & 0o077 or mode.st_uid not in (0, os.geteuid()):
            raise BootstrapError('EXECUTION_DIRECTORY_UNTRUSTED')
    return directory


def receipt_directory(directory, image):
    # Image revisions with the same model prefix and writer UID share their pending files.
    return directory / 'receipts' / (image['execution_env_prefix'] + str(image['receipt_uid']))


def credential_directory(directory, uid):
    return directory / f'reader-{uid}' if uid else directory


def prepare_receipts(directory, images):
    # Only individual files are bound read-only. The writer never mounts this private parent.
    for uid in {image['receipt_uid'] for image in images if image.get('receipt_uid')}:
        reader = credential_directory(directory, uid)
        for name in (KEY_NAME, CA_NAME):
            atomic_bytes(reader / name, read_file(directory / name, secret=True), uid=uid)
    for image in images:
        if 'receipt_uid' not in image:
            continue
        target = receipt_directory(directory, image)
        target.parent.mkdir(mode=0o700, exist_ok=True)
        parent = target.parent.lstat()
        if not stat.S_ISDIR(parent.st_mode) or parent.st_uid != os.geteuid() or parent.st_mode & 0o077:
            raise BootstrapError('EXECUTION_DIRECTORY_UNTRUSTED')
        try:
            target.mkdir(mode=0o700)
        except FileExistsError:
            pass
        else:
            os.chown(target, image['receipt_uid'], -1)
        info = target.lstat()
        if not stat.S_ISDIR(info.st_mode) or info.st_uid != image['receipt_uid'] or stat.S_IMODE(info.st_mode) != 0o700:
            raise BootstrapError('EXECUTION_DIRECTORY_UNTRUSTED')


def boot_witness_material(settings, boot, create=False):
    if str(UUID(boot)) != boot:
        raise BootstrapError('RUNTIME_BOOT_ID_INVALID')
    directory = local_directory(settings)
    directory.mkdir(parents=True, mode=0o700, exist_ok=True)
    path = directory / ('host-' + boot + '.key')
    if create and not path.exists() and not path.is_symlink():
        # Worker bootstrap holds LocalState.lock() across permit fetch and start.
        atomic_bytes(path, (token_hex(32) + '\n').encode())
    key = read_file(path, secret=True).decode().strip()
    if not KEY.fullmatch(key):
        raise BootstrapError('RUNTIME_HOST_KEY_INVALID')
    return path, key


def prepare(settings, manifest, permit=None, *, gpu=None):
    config = manifest.get('worker_runtime', {})
    if 'execution' not in config:
        previous = Path(settings.data['state_dir']) / 'execution' / 'witness-routes.json'
        if previous.exists():
            atomic_json(local_directory(settings) / previous.name, {})
        return
    directory = local_directory(settings)
    key_path, ca_path = directory / KEY_NAME, directory / CA_NAME
    ca = config['execution']['ca_pem'].encode('ascii')
    rules = witness_routes(config)
    route_file = directory / 'witness-routes.json'
    if rules:
        boot = read_file('/proc/sys/kernel/random/boot_id').decode().strip()
        path, witness_key = boot_witness_material(settings, boot)
        if permit is not None and (permit.get('witness_enrolled') is not True or witness_key == permit.get('execution_key')):
            raise BootstrapError('RUNTIME_WITNESS_NOT_ENROLLED')
        from .handoff import gpu_uuid
        witness_config = dict(boot_id=boot, gpu_uuid=gpu_uuid(gpu), key_file=str(path), ca_file=str(ca_path),
                              origin=config['execution']['origin'], socket=RUNTIME_SOCKET_DIRECTORY+'/evidence.sock',
                              release_routes_file=str(route_file))
    if permit is None:
        current = read_file(key_path, secret=True).strip()
        if not re.fullmatch(rb'[a-f0-9]{64}', current) or read_file(ca_path, secret=True) != ca:
            raise BootstrapError('EXECUTION_MATERIAL_MISMATCH')
        prepare_receipts(directory, config['images'])
        if rules or route_file.exists():
            atomic_json(route_file, rules)
        if rules:
            atomic_json(directory / 'witness.json', witness_config)
        return
    key = permit.get('execution_key')
    if not isinstance(key, str) or not KEY.fullmatch(key):
        raise BootstrapError('EXECUTION_CREDENTIAL_INVALID')
    if key_path.exists() or key_path.is_symlink():
        if not hmac.compare_digest(read_file(key_path, secret=True), (key + '\n').encode()):
            raise BootstrapError('EXECUTION_CREDENTIAL_MISMATCH')
    try:
        atomic_bytes(ca_path, ca)
        atomic_bytes(key_path, (key + '\n').encode())
        prepare_receipts(directory, config['images'])
        if rules or route_file.exists():
            atomic_json(route_file, rules)
        if rules:
            atomic_json(directory / 'witness.json', witness_config)
    except OSError:
        raise BootstrapError('EXECUTION_MATERIAL_WRITE_FAILED') from None


class CredentialStore:
    """One shared, atomically replaced file; the MaaS API reads this file directly."""
    def __init__(self, directory):
        self.state = LocalState(directory)

    def issue(self, gpu):
        # Existing entries are preserved, including preseeded production keys.
        # The broker already validated the permit and GPU binding.
        path = self.state.root / KEYS_FILENAME
        if self.state.root.resolve() != self.state.root:
            raise BootstrapError('EXECUTION_CREDENTIAL_STORE_UNAVAILABLE')
        with self.state.lock():
            try:
                keys = json.loads(read_file(path, secret=True)) if path.exists() or path.is_symlink() else {}
                if not isinstance(keys, dict) or any(not isinstance(v, str) or not KEY.fullmatch(v) for v in keys.values()):
                    raise ValueError()
                if gpu not in keys:
                    keys[gpu] = token_hex(32)
                    if len(canonical(keys)) >= MAX_BYTES:
                        raise ValueError()
                    atomic_json(path, keys)
                return keys[gpu]
            except (ValueError, OSError):
                raise BootstrapError('EXECUTION_CREDENTIAL_STORE_UNAVAILABLE') from None
