"""First-seen GPUs traverse the authenticated automatic entry without operator setup."""
from concurrent.futures import ThreadPoolExecutor
import io
import json
from pathlib import Path
from threading import Event
from uuid import uuid4
import urllib.error
import urllib.parse

from fastapi.testclient import TestClient
import pytest

from maas_bootstrap.client import BootstrapError, atomic_json
from maas_bootstrap.enrollment import StartDenied, WorkerStartBroker
from maas_bootstrap.service import create_app
from maas_bootstrap import handoff
from maas_bootstrap.worker import apply
from test_client import setup, Fetcher
from test_handoff import diskless, service, binding, permit, Runtime, GPU, OTHER_GPU, CODE


PATH = '/bootstrap/v1/worker-start/automatic'
HEADERS = {'Authorization': 'Bearer ' + CODE}


class EnrollingNative:
    """Native boundary stand-in; no pre-created GPU row or approved worker ID."""
    def __init__(self):
        self.calls = []
        self.rows = {}

    def post(self, action, body):
        self.calls.append((action, dict(body)))
        if action == 'enrollment':
            gpu = body['gpu_uuid']
            if gpu not in self.rows:
                self.rows[gpu] = {**binding(body['cluster_id']), 'worker_id':41 + len(self.rows),
                                  'worker_name':gpu, 'external_id':gpu,
                                  'state':'initializing', 'session':None}
            return self.rows[gpu]
        row = next((r for r in self.rows.values() if r['worker_id'] == body['worker_id']), None)
        if row is None or body['gpu_uuid'] != row['external_id']:
            raise StartDenied(409, 'NATIVE_HANDOFF_REJECTED')
        assert body['automatic'] is True
        if row['session'] and row['state'] != 'not_ready':
            raise StartDenied(409, 'NATIVE_HANDOFF_REJECTED')
        row.update(session=body['session_id'], state='reserved')
        return permit(body['session_id'], **{k:row[k] for k in (
            'worker_id','worker_name','external_id','cluster_id')})


def automatic(diskless, tmp_path, native, **policy):
    client, body, token, session = service(diskless, tmp_path, native, manual_grant=False)
    assert token is session is None
    path = tmp_path/'published/policy.json'
    config = json.loads(path.read_text())
    config['grants'][0].update(auto_enroll_gpus=True, **policy)
    atomic_json(path, config)
    body['boot_id'] = str(uuid4())
    assert not Path(diskless[0].data['worker_start_grant_file']).exists()
    assert not list((tmp_path/'approvals').iterdir())
    assert not native.rows
    return client, body


def test_first_gpu_is_enrolled_and_issued_without_approved_mapping_and_survives_broker_restart(diskless, tmp_path):
    native = EnrollingNative()
    client, body = automatic(diskless, tmp_path, native)
    first = client.post(PATH, json=body, headers=HEADERS)
    assert first.status_code == 200
    result = first.json()
    assert result['worker_id'] == 41 and result['external_id'] == result['worker_name'] == GPU
    assert [action for action, _ in native.calls] == ['enrollment', 'handoff']
    assert len(native.rows) == 1
    assert client.post(PATH, json=body, headers=HEADERS).json() == result
    assert 'auto_start_gpus' not in json.loads((tmp_path/'published/policy.json').read_text())['grants'][0]

    class NoNative:
        def post(self, *args): pytest.fail('Saved permit must be reused after restart')

    restarted = TestClient(create_app(tmp_path/'published', worker_start=WorkerStartBroker(
        tmp_path/'approvals', NoNative())), client=('127.0.0.1', 12345))
    assert restarted.post(PATH, json=body, headers=HEADERS).json() == result
    assert len(native.calls) == 2


@pytest.mark.parametrize('change,status', [
    ({'cluster_id':4},403), ({'image_id':'wrong-image'},403),
    ({'config_revision':'not-active'},404), ({'gpu_uuid':'GPU-short'},400),
    ({'boot_id':'not-a-uuid'},400), ({'untrusted_field':True},400),
])
def test_invalid_scope_or_identity_never_pre_enrolls(diskless, tmp_path, change, status):
    native = EnrollingNative();client, body = automatic(diskless, tmp_path, native)
    response = client.post(PATH, json=body | change, headers=HEADERS)
    assert response.status_code == status and not native.calls and not native.rows


def test_auto_enrollment_still_requires_valid_enabled_authentication(diskless, tmp_path):
    native = EnrollingNative();client, body = automatic(diskless, tmp_path, native)
    assert client.post(PATH, json=body).status_code == 401
    assert client.post(PATH, json=body, headers={'Authorization':'Bearer '+'z'*40}).status_code == 401
    path = tmp_path/'published/policy.json';policy = json.loads(path.read_text())
    policy['grants'][0]['enabled'] = False;atomic_json(path, policy)
    assert client.post(PATH, json=body, headers=HEADERS).status_code == 403
    assert not native.calls


@pytest.mark.parametrize('policy', [
    {'auto_enroll_gpus':'true'}, {'auto_enroll_gpus':1},
    {'auto_start_gpus':[]}, {'auto_start_gpus':{GPU:True}},
])
def test_malformed_auto_enrollment_policy_does_not_create_binding(diskless, tmp_path, policy):
    native = EnrollingNative();client, body = automatic(diskless, tmp_path, native)
    path = tmp_path/'published/policy.json';config = json.loads(path.read_text())
    config['grants'][0].update(policy);atomic_json(path, config)
    response = client.post(PATH, json=body, headers=HEADERS)
    assert response.status_code == 503
    assert response.json()['error']['code'] == 'WORKER_START_POLICY_INVALID'
    assert not native.calls


@pytest.mark.parametrize('wrong', [
    {'worker_id':True}, {'cluster_id':4}, {'worker_name':OTHER_GPU}, {'external_id':OTHER_GPU},
])
def test_enrollment_binding_must_match_before_issuing_permit(diskless, tmp_path, wrong):
    class WrongBinding(EnrollingNative):
        def post(self, action, body):
            return super().post(action, body) | wrong

    native = WrongBinding();client, body = automatic(diskless, tmp_path, native)
    response = client.post(PATH, json=body, headers=HEADERS)
    assert response.status_code == 503 and response.json()['error']['code'] == 'WORKER_BINDING_MISMATCH'
    assert [action for action, _ in native.calls] == ['enrollment']


def test_different_first_seen_gpus_receive_distinct_bindings_without_policy_updates(diskless, tmp_path):
    native = EnrollingNative();client, body = automatic(diskless, tmp_path, native)
    first = client.post(PATH, json=body, headers=HEADERS)
    second = client.post(PATH, json=body | {'gpu_uuid':OTHER_GPU,'boot_id':str(uuid4())}, headers=HEADERS)
    assert first.status_code == second.status_code == 200
    assert first.json()['worker_id'] != second.json()['worker_id']
    assert first.json()['external_id'] == GPU and second.json()['external_id'] == OTHER_GPU
    assert len(native.rows) == 2


def test_explicit_approved_binding_cannot_fall_back_to_creating_another_worker(diskless, tmp_path):
    native = EnrollingNative()
    client, body = automatic(diskless, tmp_path, native, auto_start_gpus={GPU:999})
    assert client.post(PATH, json=body, headers=HEADERS).status_code == 409
    assert [a for a, _ in native.calls] == ['handoff'] and not native.rows


def test_same_gpu_concurrent_first_boots_cannot_create_two_bindings_or_permits(diskless, tmp_path):
    entered, release = Event(), Event()

    class BlockingNative(EnrollingNative):
        def post(self, action, body):
            if action == 'enrollment':
                entered.set();assert release.wait(5)
            return super().post(action, body)

    native = BlockingNative();client, body = automatic(diskless, tmp_path, native)
    other = body | {'boot_id':str(uuid4())}
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(client.post, PATH, json=body, headers=HEADERS)
        try:
            assert entered.wait(5)
            assert client.post(PATH, json=body, headers=HEADERS).status_code == 409
            assert client.post(PATH, json=other, headers=HEADERS).status_code == 409
        finally:
            release.set()
        first = future.result(timeout=5)
    assert first.status_code == 200
    assert client.post(PATH, json=body, headers=HEADERS).json() == first.json()
    assert client.post(PATH, json=other, headers=HEADERS).status_code == 409
    assert len(native.rows) == 1
    assert sum(action == 'handoff' for action, _ in native.calls) == 2
    assert native.rows[GPU]['session'] == first.json()['session_id']


def test_ready_worker_rejects_new_boot_and_not_ready_reuses_same_identity(diskless, tmp_path):
    native = EnrollingNative();client, body = automatic(diskless, tmp_path, native)
    first = client.post(PATH, json=body, headers=HEADERS).json()
    row = native.rows[GPU];row['state'] = 'ready'
    next_boot = body | {'boot_id':str(uuid4())}
    assert client.post(PATH, json=next_boot, headers=HEADERS).status_code == 409
    assert row['session'] == first['session_id']
    row['state'] = 'not_ready'
    second = client.post(PATH, json=next_boot, headers=HEADERS)
    assert second.status_code == 200 and second.json()['worker_id'] == first['worker_id']
    assert second.json()['session_id'] != first['session_id'] and len(native.rows) == 1


def test_retry_after_lost_enrollment_response_reuses_committed_worker(diskless, tmp_path):
    class LostEnrollment(EnrollingNative):
        def post(self, action, body):
            result = super().post(action, body)
            if len(self.calls) == 1:
                raise BootstrapError('ENROLLMENT_UNAVAILABLE')
            return result

    native = LostEnrollment();client, body = automatic(diskless, tmp_path, native)
    assert client.post(PATH, json=body, headers=HEADERS).status_code == 503
    response = client.post(PATH, json=body, headers=HEADERS)
    assert response.status_code == 200 and response.json()['worker_id'] == 41
    assert len(native.rows) == 1
    assert [action for action, _ in native.calls] == ['enrollment','enrollment','handoff']


def test_lost_handoff_response_does_not_repeat_issuance_or_enrollment(diskless, tmp_path):
    class LostHandoff(EnrollingNative):
        def post(self, action, body):
            result = super().post(action, body)
            if action == 'handoff': raise BootstrapError('ENROLLMENT_UNAVAILABLE')
            return result

    native = LostHandoff();client, body = automatic(diskless, tmp_path, native)
    assert client.post(PATH, json=body, headers=HEADERS).status_code == 503
    response = client.post(PATH, json=body, headers=HEADERS)
    assert response.status_code == 409
    assert response.json()['error']['code'] == 'WORKER_START_NEEDS_RECONCILIATION'
    assert [action for action, _ in native.calls] == ['enrollment','handoff']


def test_e5_from_empty_state_automatically_enrolls_then_writes_external_id_and_starts(diskless, tmp_path, monkeypatch):
    native = EnrollingNative();client, body = automatic(diskless, tmp_path, native)
    settings, envelope, _ = diskless
    settings.data['worker_start_automatic'] = True
    Path(settings.data['shared_code_file']).write_text(CODE)
    original_read = handoff.read_file
    monkeypatch.setattr(handoff, 'read_file', lambda p, **kw: body['boot_id'].encode()
                        if str(p) == '/proc/sys/kernel/random/boot_id' else original_read(p, **kw))

    class Response(io.BytesIO):
        status = 200

    class Opener:
        def open(self, request, timeout):
            path = urllib.parse.urlsplit(request.full_url).path
            response = client.post(path, json=json.loads(request.data),
                                   headers={'Authorization':request.get_header('Authorization')})
            if response.status_code != 200:
                raise urllib.error.HTTPError(request.full_url,response.status_code,'',{},io.BytesIO(response.content))
            return Response(response.content)

    monkeypatch.setattr(handoff, 'https_opener', lambda _:Opener())
    runtime = Runtime();fetcher = handoff.WorkerStartFetcher()
    result = apply(settings, trigger='boot', backend=runtime, fetcher=Fetcher(envelope), permit_fetcher=fetcher)
    assert result['code'] == 'APPLIED' and result['worker_name'] == GPU and result['worker_id'] == 41
    assert (Path(settings.data['worker_data_dir'])/'external_id').read_text() == GPU
    assert apply(settings, backend=runtime, fetcher=Fetcher(envelope), permit_fetcher=fetcher)['code'] == 'UNCHANGED'
    assert runtime.creates == runtime.starts == 1 and len(native.rows) == 1
    assert [action for action, _ in native.calls] == ['enrollment','handoff']
    assert not Path(settings.data['worker_start_grant_file']).exists()
