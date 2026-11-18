"""Prepare reporting after native registration, without changing native identity."""
import json
from pathlib import Path
from uuid import uuid4

import pytest

from maas_bootstrap import cli, shutdown_auth, shutdown_events as module
from maas_bootstrap.client import BootstrapError, LocalState
from maas_bootstrap.shutdown_auth import issue
from maas_bootstrap.worker import apply
from test_client import setup, Fetcher
from test_handoff import diskless, Runtime, PermitFetcher, permit, GPU


@pytest.fixture
def prepared_worker(diskless, tmp_path, monkeypatch):
    settings, envelope, _ = diskless
    boot, session = str(uuid4()), str(uuid4())
    boot_path = tmp_path/'boot';boot_path.write_text(boot)
    monkeypatch.setattr(module,'BOOT_ID',boot_path)
    monkeypatch.setattr(module,'guard_clone_host',lambda:None)
    key = tmp_path/'event-key';key.write_text(bytes(range(32)).hex());key.chmod(0o600)
    settings.data['shutdown_events'] = dict(url='https://maas.example.invalid'+module.PATH,
        tls_certificate_file=str(Path(__file__).resolve().parents[2]/'results/maas-e5-preparation/bootstrap-server.crt'))
    scope = dict(cluster_id=3,worker_id=41,gpu_uuid=GPU,boot_id=boot,session_id=session)
    credential = issue(key,scope)
    provider = PermitFetcher(permit(session,shutdown_event_credential=credential))
    runtime = Runtime()
    return settings,envelope,runtime,provider,credential,scope


def test_success_prepares_private_scope_without_leaking_credential_to_identity_or_status(prepared_worker):
    settings,envelope,runtime,provider,credential,scope = prepared_worker
    result = apply(settings,fetcher=Fetcher(envelope),backend=runtime,permit_fetcher=provider)
    assert result['code'] == 'APPLIED' and result['shutdown_event'] == 'SHUTDOWN_EVENT_PREPARED'
    root = Path(settings.data['state_dir'])
    stored = root/'shutdown-event.json'
    assert stored.stat().st_mode & 0o777 == 0o600
    assert json.loads(stored.read_text())['scope'] == scope
    assert credential not in (root/'identity.json').read_text()
    assert credential not in json.dumps(LocalState(root).status())
    first_identity = (root/'identity.json').read_bytes()
    second = apply(settings,fetcher=Fetcher(envelope),backend=runtime,permit_fetcher=provider)
    assert second['code'] == 'UNCHANGED' and second['shutdown_event'] == 'SHUTDOWN_EVENT_PREPARED'
    assert provider.calls == runtime.creates == runtime.starts == 1
    assert first_identity == (root/'identity.json').read_bytes()


@pytest.mark.parametrize('failure',['bad_destination','missing_credential','bad_scope'])
def test_optional_notification_failure_never_changes_registration_result(prepared_worker, failure):
    settings,envelope,runtime,provider,credential,scope = prepared_worker
    if failure == 'bad_destination':
        settings.data['shutdown_events']['url'] = 'http://untrusted.invalid'
    elif failure == 'missing_credential':
        provider.result.pop('shutdown_event_credential')
    else:
        provider.result['shutdown_event_credential'] = credential.replace('mse1.', 'bad.')
    result = apply(settings,fetcher=Fetcher(envelope),backend=runtime,permit_fetcher=provider)
    assert result['code'] == 'APPLIED' and result['native_state'] == 'ready'
    assert result['shutdown_event'] in ('SHUTDOWN_EVENT_PREPARE_UNAVAILABLE','SHUTDOWN_EVENT_NOT_PREPARED')
    assert runtime.creates == runtime.starts == 1
    assert not (Path(settings.data['state_dir'])/'shutdown-event.json').exists()


def test_existing_worker_private_delivery_needs_no_native_write(prepared_worker, monkeypatch, tmp_path, capsys):
    settings,envelope,runtime,provider,credential,scope = prepared_worker
    provider.result.pop('shutdown_event_credential')
    result = apply(settings,fetcher=Fetcher(envelope),backend=runtime,permit_fetcher=provider)
    assert result['shutdown_event'] == 'SHUTDOWN_EVENT_NOT_PREPARED'
    root = Path(settings.data['state_dir'])
    original = (root/'identity.json').read_bytes()
    path = tmp_path/'delivered-token';path.write_text(credential);path.chmod(0o600)
    monkeypatch.setattr(cli,'LocalSettings',lambda _:settings)
    assert cli.main(['shutdown-prepare','--credential-file',str(path)]) == 0
    assert json.loads(capsys.readouterr().out) == {'code':'SHUTDOWN_EVENT_PREPARED'}
    assert (root/'identity.json').read_bytes() == original
    assert runtime.creates == runtime.starts == provider.calls == 1


def test_private_binding_delivery_refuses_another_boot(prepared_worker):
    settings,envelope,runtime,provider,credential,scope = prepared_worker
    apply(settings,fetcher=Fetcher(envelope),backend=runtime,permit_fetcher=provider)
    identity = LocalState(settings.data['state_dir']).read('identity.json')
    changed = identity | {'session_id':str(uuid4())}
    with pytest.raises(BootstrapError,match='BINDING_CHANGED'):
        module.prepare(settings,changed,credential)


def test_private_issuer_writes_0600_without_overwriting_or_printing_token(prepared_worker, tmp_path, monkeypatch, capsys):
    import sys
    settings,envelope,runtime,provider,credential,scope = prepared_worker
    binding_file=tmp_path/'binding.json'
    binding_file.write_text(json.dumps(scope));binding_file.chmod(0o600)
    output=tmp_path/'minted-token'
    monkeypatch.setattr(sys,'argv',['issue','--key-file',str(tmp_path/'event-key'),
                        '--binding-file',str(binding_file),'--output',str(output)])
    assert shutdown_auth.main() == 0
    assert output.stat().st_mode & 0o777 == 0o600 and output.read_text().strip() == credential
    assert credential not in capsys.readouterr().out
    output.write_text('preserve-existing')
    assert shutdown_auth.main() == 1
    assert output.read_text() == 'preserve-existing'
