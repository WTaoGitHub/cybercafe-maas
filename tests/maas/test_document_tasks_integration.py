"""Existing async API and ledger, on the disposable PostgreSQL fixture only."""
import json
from pathlib import Path
import httpx
import pytest
from maas_platform.worker import Worker
from test_integration import env, headers
from test_documents import pdf
from test_document_ocr import model

NAME = 'document.unlimited-ocr-2'


def setup(env, monkeypatch, *, status=200, incomplete=False, guard=None):
    client, service, settings, auth, revoked = env
    settings.max_running = settings.max_project_running = 1
    catalog = json.loads(settings.catalog_file.read_text());catalog[NAME] = model()
    if guard is not None:
        catalog[NAME]['execution_guard'] = True
    settings.catalog_file.write_text(json.dumps(catalog))
    bindings = json.loads(settings.bindings_file.read_text())
    for row in bindings['credentials']:
        row['models'].append(NAME)
    settings.bindings_file.write_text(json.dumps(bindings))
    factory, calls = httpx.AsyncClient, []
    def reply(request):
        body = json.loads(request.content);calls.append(body)
        if guard is not None:
            guard(request, catalog[NAME]['upstream_model'])
        count = sum(p['type'] == 'image_url' for p in body['messages'][0]['content'])
        text = ''.join(f'<PAGE><BLOCK>text[0,0,1000,500]<FORK>Reference {i+1}\n' for i in range(count))
        return httpx.Response(status, json={'choices': [{'finish_reason': 'length' if incomplete else 'stop',
            'message': {'content': text}}], 'metadata': {'doc_split_complete': True, 'doc_split_batches': (count+2)//3},
            'usage': {'prompt_tokens': 100, 'completion_tokens': 200}})
    monkeypatch.setattr('maas_platform.ocr_connector.httpx.AsyncClient',
        lambda **kw: factory(transport=httpx.MockTransport(reply), **kw))
    return client, service, settings, calls


def upload(client, data=None, media='application/pdf', key='a'):
    return client.post('/maas/v1/artifacts', headers={**headers(key), 'Content-Type': media},
                       content=pdf(2) if data is None else data)


def submit(client, artifact, *, key='a', idem='document-1', **fields):
    return client.post('/maas/v1/tasks', headers=headers(key, idem),
        json={'model': NAME, 'input': {'document': {'artifact_id': artifact}}, **fields})


def test_document_execution_guard_uses_one_attempt_for_multiple_windows(env, monkeypatch, tmp_path):
    from maas_platform.pp_ocr_execution import ATTEMPT_HEADER, PATH as CALLBACK

    gpu, key = 'GPU-12345678-1234-1234-1234-123456789abc', 'e' * 64
    attempts = []

    def guard(request, route):
        attempt = request.headers[ATTEMPT_HEADER]
        attempts.append(attempt)
        assert attempt.startswith('task.')
        event = dict(gpu_uuid=gpu, instance='f' * 32, route=route, attempt=attempt)
        for finished, expected in [(False, 204), (False, 409), (True, 204)]:
            assert client.post(CALLBACK, headers={'Authorization': 'Bearer ' + key},
                               json={**event, 'finished': finished}).status_code == expected

    client, service, settings, calls = setup(env, monkeypatch, guard=guard)
    keys = tmp_path / 'test_agent_uocr_keys'
    keys.write_text(json.dumps({gpu: key}))
    keys.chmod(0o600)
    settings.pp_ocr_worker_keys_file = keys
    artifact = upload(client, pdf(4)).json()['id']
    accepted = submit(client, artifact, idem='test_agent_uocr_four_pages')
    assert accepted.status_code == 202
    assert Worker(service).run_once()
    detail = client.get(accepted.headers['location'], headers=headers()).json()
    assert detail['status'] == 'SUCCEEDED' and detail['usage'] == {'successful_pages': 4}
    assert len(calls) == len(attempts) == 1
    with service.db.transaction() as conn:
        assert conn.execute('SELECT state, gpu_uuid FROM backend_activity').fetchone() == {
            'state': 'COMPLETED', 'gpu_uuid': gpu}
        assert conn.execute('SELECT count(*) n FROM inference_slots').fetchone()['n'] == 0


def test_existing_api_pdf_task_pages_idempotency_isolation_and_gc(env, monkeypatch):
    client, service, settings, calls = setup(env, monkeypatch)
    capability = client.get('/maas/v1/models/'+NAME+'/capabilities', headers=headers()).json()
    assert capability['usage_types'] == ['pages'] and capability['input_limits']['max_pdf_pages'] == 10
    artifact = upload(client).json()['id']
    accepted = submit(client, artifact);assert accepted.status_code == 202
    task_id = accepted.json()['id']
    with service.db.transaction() as connection:
        assert connection.execute('SELECT max_attempts FROM tasks WHERE id=%s', (task_id,)).fetchone()['max_attempts'] == 1
    assert Worker(service).run_once()
    detail = client.get('/maas/v1/tasks/'+task_id, headers=headers()).json()
    assert detail['status'] == 'SUCCEEDED' and detail['usage'] == {'successful_pages': 2}
    assert detail['result']['page_count'] == 2 and len(calls) == 1
    receipt = client.get('/maas/v1/tasks/'+task_id+'/usage', headers=headers()).json()
    assert receipt['successful_pages'] == 2 and 'successful_images' not in receipt
    assert receipt['tokens']['status'] == 'unavailable'
    assert client.get('/maas/v1/usage/'+receipt['id'], headers=headers(), params={'usage_type': 'pages'}).json() == {'successful_pages': 2}
    for kind in ('images', 'tokens'):
        response = client.get('/maas/v1/tasks/'+task_id+'/usage', headers=headers(), params={'usage_type': kind})
        assert response.status_code == 422 and response.json()['error']['code'] == 'USAGE_TYPE_UNSUPPORTED'
    assert submit(client, artifact, max_attempts=1, timeout_seconds=900).status_code == 200
    assert len(calls) == 1 and not Worker(service).run_once()
    for path in ('/maas/v1/tasks/'+task_id, '/maas/v1/artifacts/'+detail['result_artifact_id'], '/maas/v1/usage/'+receipt['id']):
        assert client.get(path, headers=headers('b')).status_code == 404
    assert submit(client, artifact, key='b', idem='foreign').status_code == 404
    with service.db.transaction() as connection:
        assert connection.execute('SELECT count(*) n FROM ocr_usage_records').fetchone()['n'] == 1
        assert connection.execute('SELECT count(*) n FROM task_usage_events').fetchone()['n'] == 1
        connection.execute("UPDATE artifacts SET expires_at=now()-interval '1 second'")
    service.cleanup()
    expired = client.get('/maas/v1/tasks/'+task_id, headers=headers()).json()
    assert expired['usage'] == {'successful_pages': 2} and expired['result'] is None
    assert expired['error']['code'] == 'RESULT_EXPIRED'
    assert client.get('/maas/v1/usage/'+receipt['id'], headers=headers()).json() == receipt


def test_invalid_mime_encryption_and_document_options_rejected(env, monkeypatch):
    client, service, _, calls = setup(env, monkeypatch)
    encrypted = Path(__file__).with_name('fixtures').joinpath('document-encrypted.pdf').read_bytes()
    for data in (b'fake', encrypted, pdf(11)):
        assert upload(client, data).status_code in (413, 422)
    artifact = upload(client, pdf(), 'application/octet-stream').json()['id']
    assert submit(client, artifact).status_code == 415
    artifact = upload(client).json()['id']
    assert submit(client, artifact, max_attempts=2).json()['error']['code'] == 'DOCUMENT_REPLAY_UNSAFE'
    assert submit(client, artifact, parameters={'dpi': 301}).status_code == 422
    assert submit(client, artifact, parameters={'custom_logit_processor': 'external'}).status_code == 422
    assert not calls
    with service.db.transaction() as connection:
        assert connection.execute('SELECT count(*) n FROM tasks').fetchone()['n'] == 0


@pytest.mark.parametrize('status,incomplete,expected', [(503, False, 'UNKNOWN_BACKEND_ACTIVITY'),
    (403, False, 'UPSTREAM_REJECTED'), (200, True, 'OUTPUT_INVALID')])
def test_no_receipt_or_replay_on_ambiguous_rejected_or_partial_results(env, monkeypatch, status, incomplete, expected):
    client, service, _, calls = setup(env, monkeypatch, status=status, incomplete=incomplete)
    artifact = upload(client).json()['id'];task_id = submit(client, artifact).json()['id']
    assert Worker(service).run_once()
    detail = client.get('/maas/v1/tasks/'+task_id, headers=headers()).json()
    assert detail['status'] == 'FAILED' and detail['error']['code'] == expected
    assert detail['usage'] is None and len(calls) == 1
    assert submit(client, artifact).status_code == 200 and not Worker(service).run_once()
    with service.db.transaction() as connection:
        assert connection.execute('SELECT count(*) n FROM ocr_usage_records').fetchone()['n'] == 0
        assert connection.execute('SELECT count(*) n FROM task_usage_events').fetchone()['n'] == 0
    if status == 503:
        assert service.metrics()['unknown_backend_activity'] == 1
        assert submit(client, artifact, idem='second').status_code == 202
        assert not Worker(service).run_once() and len(calls) == 1


def test_ledger_failure_rolls_back_document_success(env, monkeypatch):
    client, service, _, _ = setup(env, monkeypatch)
    task_id = submit(client, upload(client).json()['id']).json()['id']
    def unavailable(*args):
        raise RuntimeError('ledger unavailable')
    monkeypatch.setattr('maas_platform.metering.persist', unavailable)
    assert Worker(service).run_once()
    with service.db.transaction() as connection:
        assert connection.execute('SELECT state FROM tasks WHERE id=%s', (task_id,)).fetchone()['state'] == 'RUNNING'
        assert connection.execute('SELECT count(*) n FROM ocr_usage_records').fetchone()['n'] == 0
        assert connection.execute('SELECT count(*) n FROM task_usage_events').fetchone()['n'] == 0


@pytest.mark.parametrize('event', ['cancel', 'lease'])
def test_document_completion_after_cancel_or_lease_loss_has_no_receipt_or_replay(env, monkeypatch, event):
    client, service, _, calls = setup(env, monkeypatch)
    task_id = submit(client, upload(client).json()['id']).json()['id']
    complete = service.complete_backend
    def interrupted(task):
        complete(task)
        if event == 'cancel':
            assert client.post('/maas/v1/tasks/'+task_id+'/cancel', headers=headers()).status_code == 200
        else:
            with service.db.transaction() as connection:
                connection.execute("UPDATE tasks SET lease_until=now()-interval '1 second' WHERE id=%s", (task_id,))
    monkeypatch.setattr(service, 'complete_backend', interrupted)
    assert Worker(service).run_once()
    service.recover()
    detail = client.get('/maas/v1/tasks/'+task_id, headers=headers()).json()
    assert detail['status'] == ('CANCELLED' if event == 'cancel' else 'FAILED')
    if event == 'lease':
        assert detail['error']['code'] == 'RESULT_NOT_COMMITTED'
    assert detail['result'] is None and detail['usage'] is None
    assert not Worker(service).run_once() and len(calls) == 1
    with service.db.transaction() as connection:
        assert connection.execute('SELECT count(*) n FROM ocr_usage_records').fetchone()['n'] == 0
        assert connection.execute('SELECT count(*) n FROM task_usage_events').fetchone()['n'] == 0


def test_document_queue_shares_one_execution_slot(env, monkeypatch):
    client, service, _, calls = setup(env, monkeypatch)
    artifact = upload(client).json()['id']
    first = submit(client, artifact).json()['id']
    second = submit(client, artifact, idem='second').json()['id']
    claimed = service.claim()
    assert claimed['id'] == first and service.claim() is None
    assert client.post('/maas/v1/tasks/'+first+'/cancel', headers=headers()).status_code == 200
    assert service.finish(claimed, error=None)
    assert Worker(service).run_once() and len(calls) == 1
    assert client.get('/maas/v1/tasks/'+second, headers=headers()).json()['usage'] == {'successful_pages': 2}


@pytest.mark.parametrize('status', [200, 503])
def test_private_document_recovery_probe_is_unmetered_and_quarantines_unknown(env, monkeypatch, tmp_path, status):
    from maas_platform.maintenance import Maintenance
    from maas_platform.recovery import probe_registered_models
    from maas_platform.errors import ServiceError
    from test_document_ocr import png
    client, service, _, calls = setup(env, monkeypatch, status=status)
    image = tmp_path/'probe.png';image.write_bytes(png())
    service.pause(True, 'document maintenance trial')
    probe = lambda: probe_registered_models(service, 'a', image)
    if status == 200:
        assert Maintenance(service).resume(probe)['paused'] is False
    else:
        with pytest.raises(ServiceError) as error:
            Maintenance(service).resume(probe)
        assert error.value.code == 'UNKNOWN_BACKEND_ACTIVITY'
        assert Maintenance(service).status()['paused'] is True
        assert service.metrics()['unknown_sync_activity'] == 1
    assert len(calls) == 1
    with service.db.transaction() as connection:
        assert connection.execute('SELECT count(*) n FROM ocr_usage_records').fetchone()['n'] == 0
        assert connection.execute('SELECT count(*) n FROM sync_usage_events').fetchone()['n'] == 0
