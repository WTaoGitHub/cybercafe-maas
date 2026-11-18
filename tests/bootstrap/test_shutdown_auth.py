"""Scoped event authentication never borrows inference or registration authority."""
import base64
import json
from uuid import uuid4

import pytest
from pydantic import ValidationError

from maas_bootstrap.client import BootstrapError
from maas_bootstrap.shutdown_auth import issue
from maas_platform.errors import ServiceError
from maas_platform.shutdown_events import NodeScope, ShutdownEvent, authenticate
from test_auto_enrollment import EnrollingNative, automatic, PATH, HEADERS
from test_handoff import diskless, service, Native
from test_client import setup

SCOPE = dict(cluster_id=1, worker_id=42, gpu_uuid='GPU-aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee',
             boot_id='f591ba40-e440-4ede-9659-9ad92cdeba2b',
             session_id='ee15cb4c-0886-4ad7-a7ef-3802965d340e')
SIGNATURE = 'NkDM0YsZx6JvHj6d39uqZrp68GBMzJBo3ISr3U-CxPk'


@pytest.fixture
def key(tmp_path):
    path = tmp_path/'event-key'
    path.write_text(bytes(range(32)).hex())
    path.chmod(0o600)
    return path


def test_fixed_vector_and_node_scope_survive_more_than_48_hours(key):
    token = issue(key, SCOPE)
    assert token.split('.')[-1] == SIGNATURE
    assert authenticate('Bearer ' + token, key).model_dump(mode='json') == SCOPE
    assert 'expires_at' not in authenticate('Bearer ' + token, key).model_dump()
    assert token == issue(key, SCOPE)


@pytest.mark.parametrize('field', list(SCOPE))
def test_signed_scope_cannot_be_changed(key, field):
    token = issue(key, SCOPE)
    version, encoded, signature = token.split('.')
    scope = dict(SCOPE, **{field: 43 if field.endswith('_id') and field in ('worker_id','cluster_id') else str(uuid4())})
    encoded = base64.urlsafe_b64encode(json.dumps(scope).encode()).rstrip(b'=').decode()
    with pytest.raises(ServiceError) as error:
        authenticate('Bearer ' + '.'.join((version, encoded, signature)), key)
    assert error.value.status == 401


@pytest.mark.parametrize('authorization', ['', 'Basic nope', 'Bearer inference-key',
    'Bearer mse2.a.a', 'Bearer mse1.!.a', 'Bearer ' + 'a'*1025])
def test_invalid_credentials_are_rejected_without_secret_details(key, authorization):
    with pytest.raises(ServiceError) as error:
        authenticate(authorization, key)
    assert error.value.status == 401 and key.read_text() not in str(error.value)


def test_disabled_missing_or_public_key_file(key):
    with pytest.raises(ServiceError) as error:
        authenticate('', None)
    assert error.value.status == 404
    key.chmod(0o644)
    with pytest.raises(ServiceError) as error:
        authenticate('Bearer ' + 'x'*30, key)
    assert error.value.status == 503
    with pytest.raises(BootstrapError):
        issue(key, SCOPE)


@pytest.mark.parametrize('change', [{'cluster_id':True}, {'worker_id':0}, {'boot_id':123},
    {'session_id':None}, {'gpu_uuid':'bad'}, {'untrusted':1}])
def test_scope_schema_is_strict(change):
    with pytest.raises(ValidationError):
        NodeScope.model_validate(SCOPE | change)


def test_event_schema_requires_new_uuid_and_timezone_and_no_extra_fields():
    event = SCOPE | dict(event_id=str(uuid4()), event_type='worker.shutdown.requested',
                        occurred_at='2026-11-18T02:00:00Z')
    assert ShutdownEvent.model_validate(event).event_id != ShutdownEvent.model_validate(event | {'event_id':str(uuid4())}).event_id
    for change in ({'event_id':str(uuid4()).replace('-', '')[:12]}, {'event_id':123},
                   {'occurred_at':'2026-11-18T02:00:00'}, {'occurred_at':'2026-11-18 02:00:00+00:00'},
                   {'occurred_at':123}, {'expires_at':'fake'}):
        with pytest.raises(ValidationError):
            ShutdownEvent.model_validate(event | change)


def test_automatic_broker_attaches_scoped_credential_without_native_changes(diskless, tmp_path, key, monkeypatch):
    monkeypatch.setenv('MAAS_BOOTSTRAP_SHUTDOWN_EVENT_KEY_FILE', str(key))
    native = EnrollingNative()
    api, body = automatic(diskless, tmp_path, native)
    first = api.post(PATH, json=body, headers=HEADERS)
    assert first.status_code == 200
    result = first.json()
    scope = authenticate('Bearer ' + result['shutdown_event_credential'], key)
    assert (scope.boot_id, scope.session_id) == (NodeScope.model_validate(SCOPE | {'boot_id':body['boot_id']}).boot_id,
                                              NodeScope.model_validate(SCOPE | {'session_id':result['session_id']}).session_id)
    assert scope.gpu_uuid == body['gpu_uuid'] and scope.worker_id == result['worker_id']
    assert api.post(PATH, json=body, headers=HEADERS).json() == result
    assert [name for name, _ in native.calls] == ['enrollment', 'handoff']


def test_manual_boot_id_is_optional_and_bad_event_key_does_not_block_registration(diskless, tmp_path, key, monkeypatch):
    monkeypatch.setenv('MAAS_BOOTSTRAP_SHUTDOWN_EVENT_KEY_FILE', str(key))
    native = Native()
    api, body, token, _ = service(diskless, tmp_path, native)
    headers = {'Authorization':'Bearer ' + token}
    path = '/bootstrap/v1/worker-start'
    old = api.post(path, json=body, headers=headers)
    assert old.status_code == 200 and 'shutdown_event_credential' not in old.json()
    boot = str(uuid4())
    new = api.post(path, json=body | {'boot_id':boot}, headers=headers)
    assert new.status_code == 200 and authenticate('Bearer ' + new.json()['shutdown_event_credential'], key).boot_id.hex == boot.replace('-', '')
    assert api.post(path, json=body | {'boot_id':'invalid'}, headers=headers).status_code == 400
    calls = len(native.calls)
    key.write_text('invalid')
    failed = api.post(path, json=body | {'boot_id':boot}, headers=headers)
    assert failed.status_code == 200 and 'shutdown_event_credential' not in failed.json()
    assert len(native.calls) == calls
