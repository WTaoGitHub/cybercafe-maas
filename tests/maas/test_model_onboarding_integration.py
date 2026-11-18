"""One API, executor and disposable PostgreSQL database with both OCR adapters."""
import asyncio
import base64
import json
from pathlib import Path

import httpx
import psycopg
from psycopg.rows import dict_row
import pytest

from maas_platform.capacity import limits
from maas_platform.capacity_admission import configuration
from maas_platform.capacity_observe import publish
from maas_platform.errors import ServiceError
from maas_platform.maintenance import Maintenance
from maas_platform.recovery import probe_registered_models
from maas_platform.worker import Worker
from test_consumer_ocr import png
from test_documents import pdf
from test_integration import env, headers


IMAGE = 'ocr.default'
DOCUMENT = 'document.unlimited-ocr-2'
ADDED = 'ocr.config-test'
IMAGE_ROUTE = 'pp-ocr-fixture'
ADDED_ROUTE = 'pp-ocr-second-fixture'
EXAMPLES = Path(__file__).resolve().parents[2] / 'deploy' / 'maas'


@pytest.fixture
def unified(env, monkeypatch):
    client, service, settings, _, _ = env
    image = json.loads((EXAMPLES / 'catalog.ocr.example.json').read_text())[IMAGE]
    image.update(revision='pp-fixture-v1', upstream_model=IMAGE_ROUTE)
    models = {IMAGE: image, **json.loads((EXAMPLES / 'catalog.document.example.json').read_text())}
    settings.catalog_file.write_text(json.dumps(models))
    bindings = json.loads(settings.bindings_file.read_text())
    bindings['credentials'][0]['models'] = [IMAGE, DOCUMENT]
    bindings['credentials'][1]['models'] = [IMAGE]
    settings.bindings_file.write_text(json.dumps(bindings))
    calls, failures = [], set()
    factory = httpx.AsyncClient

    def reply(request):
        assert request.url.path == '/v1/chat/completions'
        body = json.loads(request.content)
        route = body['model']
        calls.append(route)
        images = [p for p in body['messages'][0]['content'] if p['type'] == 'image_url']
        if route == models[DOCUMENT]['upstream_model']:
            assert body['stream'] is False
            assert body['custom_logit_processor'] == 'maas.unlimited-ocr-2.ngram.v1'
            assert len(images) in (1, 2)
            if route in failures:
                return httpx.Response(503)
            text = ''.join(f'<PAGE><BLOCK>text[0,0,1000,500]<FORK>Page {i}\n'
                           for i in range(1, len(images) + 1))
        else:
            assert route in (IMAGE_ROUTE, ADDED_ROUTE) and len(images) == 1
            text = json.dumps({'text': route, 'lines': [], 'elapsed_ms': 1, 'node': 'fixture'})
        return httpx.Response(200, json={'choices': [{'finish_reason': 'stop', 'message': {'content': text}}]})

    def transport(**kwargs):
        return factory(transport=httpx.MockTransport(reply), **kwargs)

    monkeypatch.setattr('maas_platform.ocr_connector.httpx.AsyncClient', transport)
    client.app.state.sync_ingress.client_factory = transport
    return client, service, settings, calls, failures


def add_compatible_model(settings):
    models = json.loads(settings.catalog_file.read_text())
    models[ADDED] = {**models[IMAGE], 'revision': 'pp-second-fixture-v1', 'upstream_model': ADDED_ROUTE}
    settings.catalog_file.write_text(json.dumps(models))
    bindings = json.loads(settings.bindings_file.read_text())
    bindings['credentials'][0]['models'].append(ADDED)
    settings.bindings_file.write_text(json.dumps(bindings))


def sync_ocr(client, name=IMAGE, key='a'):
    image = 'data:image/png;base64,' + base64.b64encode(png()).decode()
    return client.post('/gpustack/v1/chat/completions', headers=headers(key), json={
        'model': name, 'messages': [{'role': 'user', 'content': [{'type': 'image_url', 'image_url': {'url': image}}]}],
    })


def submit_image(client, name=IMAGE, idem='image', key='a'):
    response = client.post('/maas/v1/ocr', headers=headers(key, idem), json={
        'model': name, 'image_base64': base64.b64encode(png()).decode(),
    })
    assert response.status_code == 202, response.json()
    return response.json()['id']


def submit_document(client):
    artifact = client.post('/maas/v1/artifacts', headers={**headers(), 'Content-Type': 'application/pdf'},
                           content=pdf(2))
    assert artifact.status_code == 201
    response = client.post('/maas/v1/tasks', headers=headers(idem='document'), json={
        'model': DOCUMENT, 'input': {'document': {'artifact_id': artifact.json()['id']}},
    })
    assert response.status_code == 202, response.json()
    return response.json()['id']


def check_task(client, ident, name, unit, value):
    task = client.get('/maas/v1/tasks/' + ident, headers=headers()).json()
    assert task['model'] == name and task['status'] == 'SUCCEEDED' and task['attempts'] == 1
    receipt = client.get(f'/maas/v1/tasks/{ident}/usage', headers=headers(), params={'usage_type': unit})
    assert receipt.json() == {'successful_' + unit: value}
    result = client.get('/maas/v1/artifacts/' + task['result_artifact_id'], headers=headers())
    assert result.status_code == 200
    return result.json()['outputs']


def test_two_ocr_protocols_and_config_only_third_model(unified):
    client, service, settings, calls, _ = unified
    worker = Worker(service)
    image, document = submit_image(client), submit_document(client)
    response = sync_ocr(client)
    assert response.status_code == 200
    assert json.loads(response.json()['choices'][0]['message']['content'])['text'] == IMAGE_ROUTE
    assert client.get('/maas/v1/usage/' + response.headers['X-Usage-ID'], headers=headers(),
                      params={'usage_type': 'images'}).json() == {'successful_images': 1}
    assert worker.run_once() and worker.run_once() and not worker.run_once()
    assert check_task(client, image, IMAGE, 'images', 1)['text'] == IMAGE_ROUTE
    assert check_task(client, document, DOCUMENT, 'pages', 2)['page_count'] == 2

    path = '/maas/v1/models/' + ADDED + '/capabilities'
    assert client.get(path, headers=headers()).status_code == 404
    add_compatible_model(settings)
    assert client.get(path, headers=headers()).json()['revision'] == 'pp-second-fixture-v1'
    assert client.get(path, headers=headers('b')).status_code == 403
    assert sync_ocr(client, ADDED).status_code == 200
    new = submit_image(client, ADDED, 'additional-image')
    assert worker.run_once() and not worker.run_once()
    assert check_task(client, new, ADDED, 'images', 1)['text'] == ADDED_ROUTE
    assert calls == [IMAGE_ROUTE, IMAGE_ROUTE, 'unlimited-ocr-2', ADDED_ROUTE, ADDED_ROUTE]
    with service.db.transaction() as c:
        assert c.execute('SELECT count(*) n FROM ocr_usage_records').fetchone()['n'] == 5


def test_configured_pools_share_a_global_budget(unified, tmp_path):
    client, service, settings, calls, _ = unified
    add_compatible_model(settings)
    bindings = json.loads(settings.bindings_file.read_text())
    bindings['credentials'][1]['models'].append(ADDED)
    settings.bindings_file.write_text(json.dumps(bindings))
    settings.max_running = settings.max_project_running = 2
    settings.capacity_policy_file = tmp_path / 'policy.json'
    common = {'cluster_id': 1, 'owner_principal_id': 1, 'gpu_names': ['fixture'],
              'slots_per_replica': 1, 'hard_limit': 1, 'queue_limit': 2, 'gateway_verified': True}
    pools = {name: {**common, 'model_id': i, 'routes': {name: i}}
             for i, name in enumerate((IMAGE, DOCUMENT, ADDED), 1)}
    settings.capacity_policy_file.write_text(json.dumps({'global_limit': 2, 'sync_percent': 0, 'pools': pools}))
    settings.capacity_policy_file.chmod(0o600)
    _, _, revision = configuration(settings)

    async def snapshot():
        async with await psycopg.AsyncConnection.connect(settings.database_url, autocommit=True, row_factory=dict_row) as c:
            observed = (await (await c.execute('SELECT clock_timestamp() at')).fetchone())['at']
            return await publish(c, revision, observed, {'global': limits(2, 0),
                                 'pools': {name: limits(1, 0) for name in pools}})

    service.pause(True, 'isolated capacity test')
    assert asyncio.run(snapshot()) and service.set_capacity_mode(True) == 'enforce'
    service.pause(False)
    first = submit_document(client)
    # Same PDF, different submission key, same single-slot document pool.
    with service.db.transaction() as c:
        artifact = c.execute('SELECT input_artifact_id FROM tasks WHERE id=%s', (first,)).fetchone()['input_artifact_id']
    assert client.post('/maas/v1/tasks', headers=headers(idem='document-second'), json={
        'model': DOCUMENT, 'input': {'document': {'artifact_id': artifact}},
    }).status_code == 202
    task = service.claim()
    assert task['id'] == first and service.claim() is None
    service.begin_backend(task)
    ingress = client.app.state.sync_ingress
    who = service.auth.authenticate('gpustack_registered_a')
    token = ingress.begin(who, IMAGE, 25)
    rejected = sync_ocr(client, ADDED, key='b')
    assert rejected.status_code == 429 and rejected.json()['error']['code'] == 'SYNC_CAPACITY'
    ingress.finish(token, True)
    assert sync_ocr(client, ADDED, key='b').status_code == 200
    assert calls == [ADDED_ROUTE]


def test_document_unknown_isolates_document_while_image_ocr_continues(unified, tmp_path):
    client, service, _, calls, failures = unified
    worker = Worker(service)
    prior = submit_image(client)
    assert worker.run_once()
    failures.add('unlimited-ocr-2')
    document = submit_document(client)
    queued = submit_image(client, idem='other-project', key='b')
    assert worker.run_once()
    failed = client.get('/maas/v1/tasks/' + document, headers=headers()).json()
    assert failed['status'] == 'FAILED' and failed['error']['code'] == 'UNKNOWN_BACKEND_ACTIVITY'
    assert failed['usage'] is None
    assert sync_ocr(client, key='b').status_code == 200
    assert worker.run_once() and not worker.run_once()
    assert client.get('/maas/v1/tasks/' + queued, headers=headers('b')).json()['status'] == 'SUCCEEDED'
    assert check_task(client, prior, IMAGE, 'images', 1)['text'] == IMAGE_ROUTE
    assert calls == [IMAGE_ROUTE, 'unlimited-ocr-2', IMAGE_ROUTE, IMAGE_ROUTE]
    service.pause(True, 'isolated incident')
    maintenance = Maintenance(service)
    with pytest.raises(ServiceError, match='Pause and resolve'):
        maintenance.resume(lambda: pytest.fail('Unresolved task must block recovery probes'))
    unknown, = maintenance.status()['backend_activity']
    assert unknown['task_id'] == document and unknown['state'] == 'UNKNOWN' and unknown['dispatch_route'] == 'unlimited-ocr-2'
    assert service.resolve_backend(unknown['token'], 'Test transport completed; no remote execution exists')
    failures.clear()
    probe = tmp_path / 'probe.png'
    probe.write_bytes(png())
    assert maintenance.resume(lambda: probe_registered_models(service, 'a', probe))['paused'] is False
    assert not worker.run_once()
    assert calls == [IMAGE_ROUTE, 'unlimited-ocr-2', IMAGE_ROUTE, IMAGE_ROUTE, IMAGE_ROUTE, 'unlimited-ocr-2']
    with service.db.transaction() as c:
        assert c.execute('SELECT count(*) n FROM ocr_usage_records').fetchone()['n'] == 3
