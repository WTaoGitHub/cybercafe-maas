from copy import deepcopy
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
from textwrap import dedent
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

from maas_bootstrap import execution, worker_limits
from maas_bootstrap.client import BootstrapError, ReleaseVerifier, atomic_json
from maas_bootstrap.enrollment import WorkerStartBroker
from maas_bootstrap.service import create_app
from maas_bootstrap.worker import apply
from test_client import Fetcher, setup
from test_handoff import GPU, OTHER_GPU, Runtime, PermitFetcher, binding, diskless, permit
from test_service import CODE, publish


DIGEST = 'sha256:' + 'a' * 64
OTHER_IMAGE = 'sha256:' + 'b' * 64
ENTRYPOINT = ['python3', '/opt/engine.py']
PREFIX = 'MAAS_TEST_OCR_EXECUTION_'
CERT = Path(__file__).resolve().parents[2] / 'results/maas-e5-preparation/bootstrap-server.crt'


def release(diskless, *, digest=DIGEST, memory=16384, revision='r1'):
    settings, envelope, sign = diskless
    config = {
        'profiles': {'ocr': {'cpu_millis': 4000, 'memory_mib': memory}},
        'images': [{'digest': digest, 'entrypoint': ENTRYPOINT, 'profile': 'ocr', 'execution_env_prefix': PREFIX}],
        'execution': {'origin': 'https://receipts.invalid:443', 'ca_pem': CERT.read_text()},
    }
    result = sign(dict(envelope['manifest'], config_revision=revision, worker_runtime=config))
    result['registration'].pop('token')
    return settings, result


def test_signed_policy_supports_model_changes_with_the_same_image_settings(diskless):
    settings, first = release(diskless)
    _, second = release(diskless, digest=OTHER_IMAGE, memory=8192, revision='r2')
    verifier = ReleaseVerifier()
    assert verifier.verify(settings, first)['revision'] == 'r1'
    assert verifier.verify(settings, second)['revision'] == 'r2'
    assert first['manifest']['compatible_image_ids'] == second['manifest']['compatible_image_ids']
    first['manifest']['worker_runtime']['profiles']['ocr']['memory_mib'] = 8192
    with pytest.raises(BootstrapError, match='RELEASE_VERIFICATION_FAILED'):
        verifier.verify(settings, first)


@pytest.mark.parametrize('change', ['unpinned', 'duplicate', 'entrypoint', 'invalid_cpu', 'untrusted_origin',
                                        'private_pem', 'arbitrary_field', 'missing_execution',
                                        'negative_uid', 'reserved_uid', 'bool_uid', 'unguarded_receipts'])
def test_invalid_runtime_settings_are_rejected(change, diskless):
    _, envelope = release(diskless)
    manifest = deepcopy(envelope['manifest'])
    config = manifest['worker_runtime']
    image = config['images'][0]
    if change == 'unpinned':
        image['digest'] = 'latest'
    elif change == 'duplicate':
        config['images'].append(image)
    elif change == 'entrypoint':
        image['entrypoint'] = 'python3'
    elif change == 'invalid_cpu':
        config['profiles']['ocr']['cpu_millis'] = True
    elif change == 'untrusted_origin':
        config['execution']['origin'] = 'http://receipts.invalid'
    elif change == 'private_pem':
        config['execution']['ca_pem'] += '-----BEGIN PRIVATE KEY-----'
    elif change == 'arbitrary_field':
        image['mounts'] = ['/etc']
    elif change in ('negative_uid', 'reserved_uid', 'bool_uid', 'unguarded_receipts'):
        image['receipt_uid'] = {'negative_uid': -1, 'reserved_uid': 2**32 - 1,
                                'bool_uid': True, 'unguarded_receipts': 0}[change]
        if change == 'unguarded_receipts':
            del image['execution_env_prefix'], config['execution']
    else:
        del config['execution']
    with pytest.raises(BootstrapError, match='WORKER_RUNTIME_POLICY_INVALID'):
        execution.configuration(manifest)


@pytest.mark.parametrize('local_receipts', [False, True])
def test_docker_options_and_readonly_mounts_are_scoped(tmp_path, diskless, monkeypatch, local_receipts):
    _, envelope = release(diskless)
    config = execution.configuration(envelope['manifest'])
    if local_receipts:
        config['images'][0]['receipt_uid'] = os.geteuid()
        for name in (execution.CA_NAME, execution.KEY_NAME):
            (tmp_path / name).write_bytes(b'test_agent_local_material')
            (tmp_path / name).chmod(0o600)
        execution.prepare_receipts(tmp_path, config['images'])
    original = b'class DockerDeployer:\n    def create(self):\n        if True:\n' + worker_limits.ANCHOR + b'            pass\n'
    monkeypatch.setattr(worker_limits, 'ORIGINAL_SHA256', hashlib.sha256(original).hexdigest())
    patched = worker_limits.runtime_source(original, config=config, execution_directory=tmp_path)
    compile(patched, '<runtime>', 'exec')
    assert worker_limits.runtime_source(patched, config=config, execution_directory=tmp_path) == patched
    _, next_release = release(diskless, digest=OTHER_IMAGE, memory=8192, revision='r2')
    next_config = execution.configuration(next_release['manifest'])
    run, init = object(), object()
    profiles = SimpleNamespace(RUN=run)
    docker = SimpleNamespace(types=SimpleNamespace(
        Mount=lambda target, source, **opts: {'Target': target, 'Source': source, **opts}))
    for policy, image, command, profile, name, limit in (
        (config, DIGEST, ENTRYPOINT, run, 'default', 16384),
        (next_config, OTHER_IMAGE, ENTRYPOINT, run, 'default', 8192),
        (next_config, DIGEST, ENTRYPOINT, run, 'default', 0),
        (config, DIGEST, ['/other-entrypoint'], run, 'default', 0),
        (config, DIGEST, ENTRYPOINT, init, 'default', 0),
        (config, DIGEST, ENTRYPOINT, run, 'sidecar', 0),
    ):
        code = dedent(worker_limits.configured_insertion(policy, tmp_path).decode())
        options = {'entrypoint': command, 'environment': {}, 'shm_size': 4096}
        exec(code, {'c': SimpleNamespace(profile=profile, name=name, image='registry.invalid/model@' + image),
                    'ContainerProfileEnum': profiles, 'docker': docker, 'create_options': options})
        assert options['shm_size'] == 4096
        if limit:
            assert options['mem_limit'] == options['memswap_limit'] == limit * 1024**2
            assert options['nano_cpus'] == 4_000_000_000
            assert {Path(m['Source']).name for m in options['mounts'] if m['read_only']} == {'worker.key', 'authority.pem'}
            assert all(m['type'] == 'bind' for m in options['mounts'])
            writable = [m for m in options['mounts'] if not m['read_only']]
            if local_receipts and policy is config:
                folder = execution.receipt_directory(tmp_path, config['images'][0])
                assert folder.stat().st_mode & 0o777 == 0o700
                for mount in options['mounts']:
                    if mount['read_only']:
                        source = Path(mount['Source'])
                        assert source.read_bytes() == b'test_agent_local_material'
                        assert source.stat().st_uid == os.geteuid() and source.stat().st_mode & 0o777 == 0o600
                assert writable == [dict(Target=execution.RECEIPT_TARGET, Source=str(folder), type='bind', read_only=False)]
                assert options['environment'][PREFIX + 'RECEIPT_DIR'] == execution.RECEIPT_TARGET
            else:
                assert not writable and PREFIX + 'RECEIPT_DIR' not in options['environment']
            assert options['environment'][PREFIX + 'KEY_FILE'] == '/run/maas-execution/worker.key'
        else:
            assert 'mounts' not in options and 'mem_limit' not in options and not options['environment']


class Native:
    def __init__(self):
        self.calls = []

    def post(self, action, body):
        self.calls.append(action)
        gpu = body['gpu_uuid']
        worker_id = 41 if gpu == GPU else 42
        fields = {**binding(), 'worker_id': worker_id, 'external_id': gpu, 'worker_name': gpu}
        return fields if action == 'enrollment' else permit(body['session_id'], **fields)


def test_approved_boots_issue_private_keys_consumable_by_existing_maas_map(tmp_path, diskless):
    _, envelope = release(diskless)
    config, approvals, keys_dir = (tmp_path / name for name in ('published', 'approvals', 'keys'))
    (config / 'releases').mkdir(parents=True)
    approvals.mkdir(mode=0o700)
    keys_dir.mkdir(mode=0o700)
    path = keys_dir / execution.KEYS_FILENAME
    existing = 'd' * 64
    atomic_json(path, {GPU: existing})
    atomic_json(config / 'policy.json', {'schema_version': 1, 'allowed_cidrs': ['127.0.0.1/32'], 'grants': [{
        'site_id': 'lab', 'batch_id': 'pilot', 'cluster_id': 3, 'enabled': True,
        'auto_enroll_gpus': True, 'code_sha256': hashlib.sha256(CODE.encode()).hexdigest(),
    }]})
    publish(config, envelope)
    native = Native()
    api = TestClient(create_app(config, worker_start=WorkerStartBroker(approvals, native, execution_keys_dir=keys_dir)),
                     client=('127.0.0.1', 12345))
    url = '/bootstrap/v1/worker-start/automatic'
    headers = {'Authorization': 'Bearer ' + CODE}
    data = {'cluster_id': 3, 'gpu_uuid': GPU, 'image_id': 'tower-v1', 'config_revision': 'r1', 'boot_id': str(uuid4())}
    assert api.post(url, json=data).status_code == 401
    assert api.post(url, json=data | {'cluster_id': 4}, headers=headers).status_code == 403
    assert not native.calls
    first = api.post(url, json=data, headers=headers)
    assert first.status_code == 200 and first.headers['cache-control'] == 'no-store'
    assert first.json()['execution_key'] == existing
    assert api.post(url, json=data, headers=headers).json() == first.json()
    new = api.post(url, json=data | {'gpu_uuid': OTHER_GPU}, headers=headers).json()['execution_key']
    assert execution.KEY.fullmatch(new) and new != existing
    assert native.calls == ['enrollment', 'handoff', 'enrollment', 'handoff']
    from maas_platform.config import private_text
    assert json.loads(private_text(path)) == {GPU: existing, OTHER_GPU: new}
    assert 'execution_key' not in api.get('/bootstrap/v1/config?image_id=tower-v1', headers=headers).text

    from maas_platform.errors import ServiceError
    from maas_platform.pp_ocr_execution import PATH, router

    class EmptyActivities:
        @contextmanager
        def transaction(self):
            yield SimpleNamespace(execute=lambda *_: SimpleNamespace(fetchone=lambda: None))

    receipts = FastAPI()
    receipts.add_exception_handler(ServiceError, lambda _, error: JSONResponse({'code': error.code}, status_code=error.status))
    receipts.include_router(router(EmptyActivities(), SimpleNamespace(pp_ocr_worker_keys_file=path)))
    test_receipt = {'attempt': 'sync.' + '0' * 32, 'gpu_uuid': OTHER_GPU, 'instance': '1' * 32,
                    'route': 'test_agent_ocr', 'finished': False}
    receiver = TestClient(receipts)
    denied = receiver.post(PATH, json=test_receipt, headers={'Authorization': 'Bearer ' + existing})
    assert denied.status_code == 403
    accepted = receiver.post(PATH, json=test_receipt, headers={'Authorization': 'Bearer ' + new})
    assert accepted.status_code == 409
    assert accepted.json() == {'code': 'EXECUTION_NOT_GRANTED'}  # Accepted GPU, no work was authorized.


def test_missing_store_blocks_native_issuance(tmp_path, diskless):
    _, envelope = release(diskless)
    approvals = tmp_path / 'approvals'
    approvals.mkdir(mode=0o700)
    body = {'cluster_id': 3, 'gpu_uuid': GPU, 'image_id': 'tower-v1', 'config_revision': 'r1', 'boot_id': str(uuid4())}
    releases = SimpleNamespace(release=lambda *_: envelope)
    native = Native()
    with pytest.raises(BootstrapError, match='EXECUTION_CREDENTIAL_STORE_UNAVAILABLE'):
        WorkerStartBroker(approvals, native).automatic({'auto_enroll_gpus': True}, body, releases)
    assert not native.calls


def test_material_written_before_start_and_kept_out_of_state(diskless):
    settings, envelope = release(diskless)
    directory = execution.local_directory(settings)

    class ConfiguredRuntime(Runtime):
        def start(self, settings, manifest):
            assert directory.joinpath(execution.KEY_NAME).read_text() == secret + '\n'
            assert directory.joinpath(execution.KEY_NAME).stat().st_mode & 0o777 == 0o600
            assert directory.joinpath(execution.CA_NAME).read_text() == manifest['worker_runtime']['execution']['ca_pem']
            super().start()

    runtime = ConfiguredRuntime()
    session = str(uuid4())
    secret = 'c' * 64
    arguments = {'fetcher': Fetcher(envelope), 'backend': runtime}
    with pytest.raises(BootstrapError, match='EXECUTION_CREDENTIAL_INVALID'):
        apply(settings, **arguments, permit_fetcher=PermitFetcher(permit(session)))
    assert runtime.creates == runtime.starts == 0
    provider = PermitFetcher(permit(session, execution_key=secret))
    assert apply(settings, **arguments, permit_fetcher=provider)['code'] == 'APPLIED'
    assert apply(settings, **arguments, permit_fetcher=provider)['code'] == 'UNCHANGED'
    assert provider.calls == runtime.creates == runtime.starts == 1
    assert all(secret not in path.read_text() for path in Path(settings.data['state_dir']).rglob('*.json'))


def test_pp_runtime_socket_only_mounts_on_signed_pilot_image(tmp_path, diskless):
    _, envelope = release(diskless)
    manifest = envelope['manifest']
    image = manifest['worker_runtime']['images'][0]
    image.update(execution_env_prefix='MAAS_PP_OCR_EXECUTION_', entrypoint=['python3', '/app/server.py'],
                 runtime_witness=True)
    policy = execution.configuration(manifest)
    marker = execution.RUNTIME_SOCKET_DIRECTORY
    code = dedent(worker_limits.configured_insertion(policy, tmp_path).decode())
    run = object()
    for image_id, enabled in ((DIGEST, True), (OTHER_IMAGE, False)):
        options = {'entrypoint': image['entrypoint'], 'environment': {}}
        exec(code, {'c': SimpleNamespace(profile=run, name='default', image='registry.invalid/model@'+image_id),
                    'ContainerProfileEnum': SimpleNamespace(RUN=run), 'create_options': options,
                    'docker': SimpleNamespace(types=SimpleNamespace(
                        Mount=lambda target, source, **opts: {'Target': target, 'Source': source, **opts}))})
        sockets = [m for m in options.get('mounts', ()) if m['Target'] == marker]
        assert sockets == ([dict(Target=marker, Source=marker, type='bind', read_only=True)] if enabled else [])
        assert ('MAAS_PP_OCR_EXECUTION_RUNTIME_SOCKET' in options['environment']) is enabled
    image['entrypoint'] = ENTRYPOINT
    with pytest.raises(BootstrapError, match='WORKER_RUNTIME_POLICY_INVALID'):
        execution.configuration(manifest)


def test_pp_witness_container_loses_privilege_before_gpu_mapping(tmp_path, monkeypatch, diskless):
    _, envelope = release(diskless)
    manifest = envelope['manifest']
    image = manifest['worker_runtime']['images'][0]
    image.update(execution_env_prefix='MAAS_PP_OCR_EXECUTION_', entrypoint=['python3', '/app/server.py'],
                 runtime_witness=True)
    policy = execution.configuration(manifest)
    original = (b'class DockerDeployer:\n    def create(self):\n        if True:\n'
                + worker_limits.RESOURCE_ANCHOR + b'            map_gpu()\n'
                + worker_limits.ANCHOR + b'            pass\n')
    monkeypatch.setattr(worker_limits, 'ORIGINAL_SHA256', hashlib.sha256(original).hexdigest())
    args = dict(config=policy, execution_directory=tmp_path)
    access = worker_limits.witness_container_access(policy)
    old = original.replace(worker_limits.ANCHOR, worker_limits.configured_insertion(policy, tmp_path)
                           + worker_limits.ANCHOR)
    applied = worker_limits.runtime_source(old, **args)
    assert access + worker_limits.RESOURCE_ANCHOR in applied
    assert worker_limits.runtime_source(applied, **args) == applied
    assert worker_limits.runtime_source(applied, enabled=False, **args) == original

    run = object()
    program = compile(dedent(access.decode()), '<access>', 'exec')

    def create(digest, resources, entrypoint):
        options = {'entrypoint': entrypoint, 'privileged': True}
        exec(program, {'c': SimpleNamespace(profile=run, name='default', image='registry.invalid/model@'+digest,
                                            resources=resources),
                       'ContainerProfileEnum': SimpleNamespace(RUN=run), 'create_options': options})
        return options['privileged']

    assert not create(DIGEST, {'gpu': '0'}, image['entrypoint'])
    assert create(OTHER_IMAGE, {'gpu': '0'}, image['entrypoint'])
    assert create(DIGEST, {'gpu': '0'}, ENTRYPOINT)
    with pytest.raises(ValueError, match='isolated GPU container'):
        create(DIGEST, {'gpu': 'all'}, image['entrypoint'])


def test_model_witness_enrollment_pins_the_first_approved_boot_key(tmp_path, diskless, monkeypatch):
    from maas_bootstrap.enrollment import StartDenied
    from maas_bootstrap.runtime_witness import current_routes

    settings, envelope = release(diskless)
    manifest = envelope['manifest']
    rule, = manifest['worker_runtime']['images']
    rule.update(receipt_uid=os.geteuid(), runtime_witness={'routes': ['test-new-engine']})
    boot = str(uuid4())
    real_read = execution.read_file
    monkeypatch.setattr(execution, 'read_file', lambda path, **kw:
                        boot.encode() if str(path) == '/proc/sys/kernel/random/boot_id' else real_read(path, **kw))
    path, key = execution.boot_witness_material(settings, boot, create=True)
    approvals, keys, hosts = [tmp_path / n for n in ('approvals', 'model-keys', 'host-keys')]
    for folder in (approvals, keys, hosts):
        folder.mkdir(mode=0o700)
    native = Native()
    broker = WorkerStartBroker(approvals, native, execution_keys_dir=keys, witness_keys_dir=hosts)
    request = dict(cluster_id=3, gpu_uuid=GPU, image_id='tower-v1', config_revision='r1', boot_id=boot)
    releases = SimpleNamespace(release=lambda *_: envelope)
    def start(**extra):
        return broker.automatic({'auto_enroll_gpus': True}, request | extra, releases)

    with pytest.raises(StartDenied, match='RUNTIME_WITNESS_KEY_REQUIRED'):
        start()
    assert not native.calls
    response = start(witness_key=key)
    assert response['witness_enrolled'] and key != response['execution_key'] and key not in json.dumps(response)
    assert start(witness_key=key) == response
    with pytest.raises(StartDenied, match='RUNTIME_WITNESS_BINDING_CONFLICT'):
        start(witness_key='b'*64)
    assert (hosts / f'{GPU}.{boot}.key').read_text().strip() == path.read_text().strip() == key
    assert all(key not in f.read_text() for f in approvals.rglob('*.json'))
    execution.prepare(settings, manifest, response, gpu=GPU)
    cfg = json.loads((execution.local_directory(settings) / 'witness.json').read_text())
    assert cfg['boot_id'] == boot and cfg['gpu_uuid'] == GPU and cfg['key_file'] == str(path)
    assert current_routes(cfg) == {'test-new-engine': dict(digest=DIGEST, entrypoint=ENTRYPOINT, execution_env_prefix=PREFIX)}
    assert key not in worker_limits.configured_insertion(manifest['worker_runtime'], path.parent).decode()
    assert str(path) not in worker_limits.configured_insertion(manifest['worker_runtime'], path.parent).decode()
    rule['runtime_witness']['routes'].append('test-new-engine')
    with pytest.raises(BootstrapError, match='WORKER_RUNTIME_POLICY_INVALID'):
        execution.configuration(manifest)
