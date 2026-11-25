import asyncio
import base64
from concurrent.futures import ThreadPoolExecutor
from contextlib import nullcontext
import importlib.util
import json
from pathlib import Path
from threading import Event
from types import SimpleNamespace
from urllib.error import HTTPError, URLError

from fastapi import HTTPException
import httpx
import pytest

from maas_platform.pp_ocr_execution import ATTEMPT_HEADER, PATH, TABLE
from maas_platform.worker import Worker
from test_consumer_ocr import png
from test_integration import env, headers, sync_setup


@pytest.fixture
def guarded(env, tmp_path, monkeypatch):
    def legacy_gateway(_):
        pytest.fail('Guarded test did not install its gateway fixture')

    client, service, settings = sync_setup(env, monkeypatch, legacy_gateway)
    catalog = json.loads(settings.catalog_file.read_text())
    catalog['ocr.default']['execution_guard'] = True
    settings.catalog_file.write_text(json.dumps(catalog))
    keys = {f'GPU-{digit * 8}-{digit * 4}-{digit * 4}-{digit * 4}-{digit * 12}': digit * 64 for digit in '12'}
    path = tmp_path / 'test_agent_worker_keys'
    path.write_text(json.dumps(keys))
    path.chmod(0o600)
    settings.pp_ocr_worker_keys_file = path
    worker_module = Path(__file__).resolve().parents[2] / 'gpu-ocr' / 'execution_guard.py'
    spec = importlib.util.spec_from_file_location('test_agent_ocr_guard', worker_module)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    replicas = [module.ExecutionGuard('https://test-agent-worker.test', key, gpu, 'real-ocr') for gpu, key in keys.items()]

    def callback(request, timeout):
        response = client.post(PATH, headers=dict(request.header_items()), content=request.data)
        if response.status_code != 204:
            raise HTTPError(request.full_url, response.status_code, '', {}, None)
        return nullcontext(SimpleNamespace(status=response.status_code))

    for replica in replicas:
        replica.open = callback
    return client, service, replicas


@pytest.mark.parametrize('kind', ['task', 'sync'])
@pytest.mark.parametrize('scenario', ['result', 'disconnect', 'duplicate_response', 'http_200_without_witness'])
def test_execution_guard_deduplicates_across_gpu_replicas_and_reclaims_after_finish(guarded, monkeypatch, kind, scenario):
    client, service, (first, other) = guarded
    started, release = Event(), Event()
    calls, attempts, owner = [], [], []

    def engine(image):
        calls.append(image)
        started.set()
        assert release.wait(5)
        return {'text': 'test_agent_text', 'lines': [], 'elapsed_ms': 1, 'node': 'test_agent_worker'}

    def gateway(request):
        attempt = request.headers[ATTEMPT_HEADER]
        assert attempt.startswith(kind + '.')
        attempts.append(attempt)
        if scenario == 'http_200_without_witness':
            return httpx.Response(200, json={'choices': []})
        owner.append(pool.submit(first.run, attempt, engine, 'test_agent_image'))
        assert started.wait(5)
        with pytest.raises(HTTPException) as duplicate:
            other.run(attempt, engine, 'duplicate')
        assert duplicate.value.status_code == 409
        with pytest.raises(HTTPException) as foreign_finish:
            other.report(attempt, finished=True)
        assert foreign_finish.value.status_code == 409
        if scenario == 'disconnect':
            raise httpx.ReadError('test_agent_disconnect', request=request)
        release.set()
        result = owner[0].result(5)
        if scenario == 'duplicate_response':
            return httpx.Response(409)
        return httpx.Response(200, json={'choices': [{'message': {'content': json.dumps(result)}}]})

    def row():
        with service.db.transaction() as conn:
            return conn.execute(f'SELECT * FROM {TABLE[kind]} WHERE token=%s', (attempts[0].split('.')[1],)).fetchone()

    key = headers(idem='test_agent_guarded_request')
    key[ATTEMPT_HEADER] = 'caller-supplied-marker'
    image = base64.b64encode(png()).decode()
    original_client = httpx.AsyncClient

    async def transport(request):
        return await asyncio.to_thread(gateway, request)

    def factory(**kwargs):
        return original_client(transport=httpx.MockTransport(transport), **kwargs)

    monkeypatch.setattr('maas_platform.ocr_connector.httpx.AsyncClient', factory)
    client.app.state.sync_ingress.client_factory = factory
    with ThreadPoolExecutor(max_workers=1) as pool:
        try:
            if kind == 'sync':
                body = {'model': 'ocr.default', 'messages': [{'role': 'user', 'content': [
                    {'type': 'image_url', 'image_url': {'url': 'data:image/png;base64,' + image}}]}]}
                response = client.post('/gpustack/v1/chat/completions', headers=key, json=body)
                expected = {'result': 200, 'duplicate_response': 502}
                assert response.status_code == expected.get(scenario, 503)
            else:
                accepted = client.post('/maas/v1/tasks', headers=key, json={'model': 'ocr.default',
                    'input': {'image': {'image_base64': image}}, 'max_attempts': 2})
                assert accepted.status_code == 202
                assert Worker(service).run_once()
                response = client.get(accepted.headers['location'], headers=key)
                assert response.json()['status'] == ('SUCCEEDED' if scenario == 'result' else 'FAILED')
            if scenario == 'duplicate_response':
                assert response.json()['error']['code'] == 'BACKEND_RESULT_UNAVAILABLE'
            assert len(attempts) == 1
            if scenario == 'http_200_without_witness':
                assert row()['state'] == 'UNKNOWN' and row()['gpu_uuid'] is None
                assert not calls
                with pytest.raises(HTTPException) as delayed:
                    first.run(attempts[0], engine, 'test_agent_delayed')
                assert delayed.value.status_code == 409
                return
            assert row()['gpu_uuid'] == first.identity['gpu_uuid']
            assert row()['gpu_instance'] == first.identity['instance']
            assert (row()['state'], row()['gpu_finished_at'] is not None) == (
                ('UNKNOWN', False) if scenario == 'disconnect' else ('COMPLETED', True))
            assert service.metrics()['unknown_' + ('sync' if kind == 'sync' else 'backend') + '_activity'] == (scenario == 'disconnect')
            release.set()
            owner[0].result(5)
            assert row()['state'] == 'COMPLETED' and row()['gpu_finished_at'] is not None
            assert service.metrics()['unknown_' + ('sync' if kind == 'sync' else 'backend') + '_activity'] == 0
            terminal_at = row()['gpu_finished_at']
            first.report(attempts[0], finished=True)
            assert row()['gpu_finished_at'] == terminal_at and calls == ['test_agent_image']
            with service.db.transaction() as conn:
                assert conn.execute('SELECT count(*) n FROM ocr_usage_records').fetchone()['n'] == (scenario == 'result')
        finally:
            release.set()


def test_concurrent_start_only_one_replica_gets_a_grant_and_safe_retry_of_lost_ack(guarded):
    client, service, (first, other) = guarded
    ingress = client.app.state.sync_ingress
    who = service.auth.authenticate('gpustack_registered_a')
    attempt = 'sync.' + ingress.begin(who, 'ocr.default', 5)
    bad = {**first.identity, 'attempt': attempt, 'finished': False}
    assert client.post(PATH, json=bad, headers=other.headers).status_code == 403
    with ThreadPoolExecutor(max_workers=2) as pool:
        def start(replica):
            try:
                replica.report(attempt, finished=False)
                return replica
            except HTTPException as exc:
                assert exc.status_code == 409

        winners = [gpu for gpu in pool.map(start, (first, other)) if gpu]
    assert len(winners) == 1
    with pytest.raises(HTTPException, match='409'):
        winners[0].report(attempt, finished=False)
    winners[0].report(attempt, finished=True)
    ingress.finish(attempt.split('.')[1], True)

    token = ingress.begin(who, 'ocr.default', 5)
    call = first.open
    observed = []

    def lose_ack(request, timeout):
        response = call(request, timeout)
        observed.append(json.loads(request.data)['finished'])
        if observed == [False, True]:
            raise URLError('test_agent_lost_terminal_ack')
        return response

    first.open = lose_ack
    assert first.run('sync.' + token, lambda _: 'done', None) == 'done'
    assert observed == [False, True, True]
    with service.db.transaction() as conn:
        assert conn.execute('SELECT backend_finished FROM inference_slots').fetchone()['backend_finished']


def test_worker_has_no_gpu_call_without_authorization(guarded):
    client, _, (first, _) = guarded
    for marker, code in (('', 403), ('task.' + '7' * 32, 409)):
        with pytest.raises(HTTPException) as denied:
            first.run(marker, lambda _: pytest.fail('Unauthorized GPU execution'), None)
        assert denied.value.status_code == code
    assert client.post(PATH, content=b'x' * 4097).status_code == 413


def test_engine_exception_keeps_grant_and_expired_dispatch_cannot_start(guarded):
    client, service, (first, other) = guarded
    settings = service.settings
    settings.max_running = settings.max_project_running = 2
    ingress = client.app.state.sync_ingress
    who = service.auth.authenticate('gpustack_registered_a')
    token = ingress.begin(who, 'ocr.default', 5)
    expired = ingress.begin(who, 'ocr.default', 5)
    with service.db.transaction() as conn:
        conn.execute("UPDATE sync_activity SET deadline=now()-interval '1 second' WHERE token=%s", (expired,))
    with pytest.raises(HTTPException, match='409'):
        first.run('sync.' + expired, lambda _: pytest.fail('Expired dispatch started'), None)
    event = {**first.identity, 'attempt': 'sync.' + token, 'finished': False, 'route': 'test_agent_wrong_route'}
    assert client.post(PATH, json=event, headers=first.headers).status_code == 409

    def engine(_):
        raise RuntimeError('test_agent_uncertain_gpu_failure')

    with pytest.raises(RuntimeError, match='uncertain_gpu_failure'):
        first.run('sync.' + token, engine, None)
    ingress.finish(token, False)
    with pytest.raises(HTTPException, match='409'):
        other.run('sync.' + token, lambda _: pytest.fail('Unconfirmed attempt ran twice'), None)
    with service.db.transaction() as conn:
        row = conn.execute('SELECT state,gpu_uuid,gpu_finished_at FROM sync_activity WHERE token=%s', (token,)).fetchone()
    assert row == {'state': 'UNKNOWN', 'gpu_uuid': first.identity['gpu_uuid'], 'gpu_finished_at': None}


def test_finish_receipt_survives_lost_callbacks(guarded, tmp_path, monkeypatch):
    import hashlib
    import hmac

    _, _, (replica, _) = guarded
    replica.receipt_dir = tmp_path / 'test_agent_receipts'
    replica.receipt_dir.mkdir(mode=0o700)
    attempt = 'task.' + 'a' * 32
    calls = []

    def unavailable(request, timeout):
        calls.append(request)
        raise URLError('test_agent_offline')

    with monkeypatch.context() as patch:
        patch.setattr(replica, 'open', unavailable)
        with pytest.raises(HTTPException, match='503'):
            replica.report_finished(attempt)
    assert len(calls) == 2
    path = replica.receipt_dir / attempt
    signature, payload = path.read_bytes().split(b'\n', 1)
    assert json.loads(payload) == {**replica.identity, 'attempt': attempt, 'finished': True}
    assert hmac.compare_digest(signature.decode(), hmac.new(
        ('1' * 64).encode(), b'maas-gpu-finish-v1\0' + payload, hashlib.sha256).hexdigest())
    monkeypatch.setattr(replica, 'open', lambda *_args, **_kw: nullcontext(SimpleNamespace(status=204)))
    replica.report_finished(attempt)
    assert not list(replica.receipt_dir.iterdir())
    # A failed volume still permits the normal callback to confirm engine termination.
    replica.receipt_dir.rmdir()
    replica.report_finished(attempt)

def test_stalled_receipt_volume_still_reports_termination(guarded, tmp_path, monkeypatch):
    _, _, (replica, _) = guarded
    replica.receipt_dir = tmp_path
    release, exited = Event(), Event()
    writes, callbacks = [], []
    def slow_save(attempt):
        writes.append(attempt)
        try:
            assert release.wait(5)
        finally:
            exited.set()
    monkeypatch.setattr(replica, 'save_finish', slow_save)
    monkeypatch.setattr(replica, 'report', lambda attempt, *, finished: callbacks.append((attempt, finished)))
    tokens = ['task.' + char*32 for char in 'ab']
    try:
        for token in tokens:
            replica.report_finished(token)
        assert callbacks == [(token, True) for token in tokens]
        assert writes == tokens[:1] and not exited.is_set()
    finally:
        release.set()
        assert exited.wait(2)
