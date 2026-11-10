import json
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace
from uuid import uuid4

import pytest

from maas_bootstrap import delivery
from maas_bootstrap.client import BootstrapError, LocalSettings, atomic_json
from maas_bootstrap.enrollment import WorkerStartBroker
from maas_bootstrap.worker import apply
from test_client import Fetcher, setup
from test_handoff import GPU, Native, Runtime, diskless


CLONE = """
import json
from pathlib import Path
import sys
from maas_bootstrap import cli, delivery, worker
folder = Path(sys.argv[1])
sys.path.insert(0, str(Path.cwd() / 'tests/bootstrap'))
from test_handoff import Fetcher, Runtime

delivery.GRANT_PATH = folder / 'boot-grant'
delivery.BOOT_ID_PATH = folder / 'boot-id'
delivery.HTTPSConfigFetcher = lambda: Fetcher(json.loads((folder / 'release.json').read_bytes()))
worker.NativeDockerWorker = Runtime
worker.run = lambda args: 'tmpfs' if args[0] == 'findmnt' else ''
target = delivery.target

def receive(*args):
    result = target(*args)
    if (folder / 'drop-ack').exists():
        raise SystemExit(255)
    return result

delivery.target = receive
raise SystemExit(cli.main(['--config', str(folder / 'bootstrap.json'), 'receive-grant']))
"""


@pytest.fixture
def endpoints(diskless, tmp_path, monkeypatch):
    settings, envelope, _ = diskless
    clone = tmp_path / 'clone'
    clone.mkdir(mode=0o700)
    settings.data['worker_start_grant_file'] = str(clone / 'boot-grant')
    Path(settings.data['worker_data_dir']).mkdir(mode=0o700)
    atomic_json(clone / 'bootstrap.json', settings.data)
    atomic_json(clone / 'release.json', envelope)
    boot_id = str(uuid4())
    (clone / 'boot-id').write_text(boot_id)

    popen = subprocess.Popen

    def fake_ssh(argv, **options):
        assert argv[0] == 'ssh' and 'StrictHostKeyChecking=yes' in argv
        assert argv[-3:] == ['pilot-clone', '/opt/maas/bin/maas-worker-bootstrap', 'receive-grant']
        return popen([sys.executable, '-c', CLONE, str(clone)], **options)

    monkeypatch.setattr(delivery.subprocess, 'Popen', fake_ssh)
    approvals = tmp_path / 'approvals'
    approvals.mkdir(mode=0o700)
    request = dict(root=approvals, operation_dir=tmp_path / 'handoff', ssh_host='pilot-clone',
                   boot_id=boot_id, gpu=GPU, cluster_id=3, config_revision='r1', worker_id=41,
                   previous_session_id=str(uuid4()), fence_receipt='operator verified exit')
    return clone, approvals, request


def test_delivery_reuses_approval_when_ssh_ack_is_lost(endpoints):
    clone, approvals, request = endpoints
    (clone / 'drop-ack').touch()
    with pytest.raises(BootstrapError, match='DELIVERY_SSH_UNCONFIRMED'):
        delivery.deliver(**request)

    grant = clone / 'boot-grant'
    raw = grant.read_bytes()
    session_id = delivery.grant_session(raw)
    assert grant.stat().st_mode & 0o777 == 0o600
    (clone / 'drop-ack').unlink()
    result = delivery.deliver(**request)
    assert result == {'code': 'GRANT_DELIVERED', 'worker_id': 41, 'session_id': session_id}
    assert grant.read_bytes() == raw
    assert list(approvals.iterdir()) == [approvals / session_id]
    record = json.loads((approvals / session_id / 'grant.json').read_bytes())
    assert record['previous_session_id'] == request['previous_session_id']
    assert raw.decode().strip() not in json.dumps(result)

    with pytest.raises(BootstrapError, match='DELIVERY_NEEDS_RECONCILIATION'):
        delivery.deliver(**(request | {'operation_dir': request['operation_dir'].with_name('other')}))
    assert list(approvals.iterdir()) == [approvals / session_id]


@pytest.mark.parametrize('changed', [
    {'boot_id': '00000000-0000-0000-0000-000000000001'},
    {'gpu': 'GPU-aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee'},
    {'cluster_id': 7},
    {'config_revision': 'other'},
])
def test_unexpected_clone_cannot_receive_a_grant(endpoints, changed):
    clone, approvals, request = endpoints
    with pytest.raises(BootstrapError, match='DELIVERY_TARGET_CHANGED'):
        delivery.deliver(**(request | changed))
    assert not list(approvals.iterdir())
    assert not (clone / 'boot-grant').exists()


def test_bootstrap_requires_a_grant_and_starts_only_the_delivered_session(endpoints):
    clone, approvals, request = endpoints
    settings = LocalSettings(clone / 'bootstrap.json')
    envelope = json.loads((clone / 'release.json').read_bytes())
    runtime = Runtime()
    native = Native()
    broker = WorkerStartBroker(approvals, native)

    def boot():
        return apply(settings, trigger='boot', backend=runtime, fetcher=Fetcher(envelope))

    with pytest.raises(BootstrapError, match='LOCAL_FILE_UNAVAILABLE'):
        boot()
    assert runtime.creates == 0 and not native.calls

    delivered = delivery.deliver(**request)
    body = dict(cluster_id=3, gpu_uuid=GPU, image_id='tower-v1', config_revision='r1')
    grant = (clone / 'boot-grant').read_text().strip()
    releases = SimpleNamespace(release=lambda *_: envelope)
    permits = SimpleNamespace(fetch=lambda *_: broker.start('Bearer ' + grant, body, releases))
    result = apply(settings, trigger='boot', backend=runtime, fetcher=Fetcher(envelope), permit_fetcher=permits)
    assert result['session_id'] == delivered['session_id'] and result['worker_id'] == 41
    assert result['trigger'] == 'boot' and result['native_state'] == 'ready'
    assert runtime.creates == runtime.starts == len(native.calls) == 1
    assert native.calls[0][1]['previous_session_id'] == request['previous_session_id']


def test_original_operation_cannot_deliver_to_another_boot(endpoints):
    clone, approvals, request = endpoints
    result = delivery.deliver(**request)
    (clone / 'boot-grant').unlink()
    (clone / 'boot-id').write_text(str(uuid4()))

    with pytest.raises(BootstrapError, match='DELIVERY_TARGET_CHANGED'):
        delivery.deliver(**request)
    assert not (clone / 'boot-grant').exists()
    assert list(approvals.iterdir()) == [approvals / result['session_id']]
