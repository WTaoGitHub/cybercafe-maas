"""Run with a disposable PostgreSQL database ending in _test."""
import json
from pathlib import Path

import httpx
import pytest

from maas_platform.ovis_ocr import PLAIN_OCR_PROMPT
from maas_platform.pp_ocr_execution import ATTEMPT_HEADER, PATH
from maas_platform.worker import Worker
from test_integration import env, headers


def test_ovis_rejects_inline_input_at_submission(env):
    client, service, settings, _, _ = env
    catalog = settings.catalog()
    catalog.update(json.loads(Path('deploy/maas/catalog.ovisocr3.example.json').read_text()))
    settings.catalog_file.write_text(json.dumps(catalog))
    bindings = json.loads(settings.bindings_file.read_text())
    bindings['credentials'][0]['models'].append('ocr.ovisocr3')
    settings.bindings_file.write_text(json.dumps(bindings))

    response = client.post('/maas/v1/tasks', headers=headers(idem='test_agent_ovis_inline'),
                           json={'model': 'ocr.ovisocr3', 'input': {'image': {'image_base64': 'AA=='}}})
    assert response.status_code == 422 and response.json()['error']['code'] == 'INPUT_INVALID'
    with service.db.transaction() as conn:
        assert conn.execute('SELECT count(*) AS n FROM tasks').fetchone()['n'] == 0


@pytest.mark.parametrize('finish_reason, guarded', [('stop', False), ('length', False), ('stop', True), ('disconnect', True)])
def test_ovis_plain_text_task_and_receipt(env, monkeypatch, tmp_path, finish_reason, guarded):
    client, service, settings, auth, _ = env
    catalog = settings.catalog()
    catalog.update(json.loads(Path('deploy/maas/catalog.ovisocr3.example.json').read_text()))
    if guarded:
        catalog['ocr.ovisocr3']['execution_guard'] = True
    gpu = 'GPU-11111111-1111-1111-1111-111111111111'
    worker_key = '2' * 64
    if guarded:
        settings.pp_ocr_worker_keys_file = tmp_path / 'test_agent_worker_keys.json'
        settings.pp_ocr_worker_keys_file.write_text(json.dumps({gpu:worker_key}))
        settings.pp_ocr_worker_keys_file.chmod(0o600)
    settings.catalog_file.write_text(json.dumps(catalog))
    bindings = json.loads(settings.bindings_file.read_text())
    bindings['credentials'][0]['models'].append('ocr.ovisocr3')
    settings.bindings_file.write_text(json.dumps(bindings))
    calls = []
    original = httpx.AsyncClient
    events = []

    def callback(event, finished):
        assert client.post(PATH, json={**event, 'finished':finished},
                           headers={'Authorization':'Bearer '+worker_key}).status_code == 204

    def infer(request):
        body = json.loads(request.content)
        assert body['model'] == 'ovisocr3' and body['stream'] is False
        assert body['chat_template_kwargs'] == {'enable_thinking': False}
        image, prompt = body['messages'][0]['content']
        assert image['image_url']['url'].startswith('data:image/png;base64,')
        assert prompt == {'type': 'text', 'text': PLAIN_OCR_PROMPT}
        calls.append(body)
        if guarded:
            event = {'attempt':request.headers[ATTEMPT_HEADER], 'gpu_uuid':gpu, 'instance':'3'*32, 'route':'ovisocr3'}
            assert event['attempt'].startswith('task.')
            events.append(event)
            callback(event, False)
            if finish_reason == 'disconnect':
                raise httpx.ReadError('test_agent_disconnect', request=request)
            callback(event, True)
        else:
            assert ATTEMPT_HEADER not in request.headers
        return httpx.Response(200, json={'choices': [{'finish_reason': finish_reason, 'message': {'content': '128\n12 minutes'}}],
                                         'usage': {'prompt_tokens': 8, 'completion_tokens': 4, 'total_tokens': 12}})

    monkeypatch.setattr('maas_platform.ocr_connector.httpx.AsyncClient',
                        lambda **kw: original(transport=httpx.MockTransport(infer), **kw))
    upload = client.post('/maas/v1/artifacts', headers={**headers(), 'Content-Type': 'image/png'},
                         content=Path('tests/ovisocr3/fixtures/service-report.png').read_bytes())
    assert upload.status_code == 201
    response = client.post('/maas/v1/tasks', headers=headers(idem='test_agent_ovis_plain'),
                           json={'model': 'ocr.ovisocr3', 'max_attempts': 1,
                                 'input': {'image': {'artifact_id': upload.json()['id']}}})
    assert response.status_code == 202 and Worker(service).run_once() and len(calls) == 1
    ident = response.json()['id']
    identity = auth.authenticate('gpustack_registered_a')
    row = service.get(identity, ident)
    if finish_reason == 'disconnect':
        assert row['state'] == 'FAILED' and row['error_code'] == 'UNKNOWN_BACKEND_ACTIVITY'
        assert service.metrics()['unknown_backend_activity'] == 1
        callback(events[0], True)
        assert service.metrics()['unknown_backend_activity'] == 0
        assert service.get(identity, ident)['usage'] is None
        return
    assert service.metrics()['unknown_backend_activity'] == 0
    if finish_reason == 'length':
        assert row['state'] == 'FAILED' and row['error_code'] == 'OUTPUT_INVALID' and row['usage'] is None
        return
    assert row['state'] == 'SUCCEEDED' and row['attempts'] == 1
    data, _ = service.artifact(identity, row['result_artifact_id'])
    assert json.loads(data)['outputs'] == {'text': '128\n12 minutes'}
    assert row['usage']['successful_images'] == 1
    assert row['usage']['tokens']['input_tokens'] == 8
    assert service.metrics()['successful_tasks'] == 1
