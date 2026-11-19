"""Run with a disposable PostgreSQL database ending in _test."""
import json
from pathlib import Path

import httpx
import pytest

from maas_platform.ovis_ocr import PLAIN_OCR_PROMPT
from maas_platform.worker import Worker
from test_integration import env, headers


@pytest.mark.parametrize('finish_reason', ['stop', 'length'])
def test_ovis_plain_text_task_and_receipt(env, monkeypatch, finish_reason):
    client, service, settings, auth, _ = env
    catalog = settings.catalog()
    catalog.update(json.loads(Path('deploy/maas/catalog.ovisocr3.example.json').read_text()))
    settings.catalog_file.write_text(json.dumps(catalog))
    bindings = json.loads(settings.bindings_file.read_text())
    bindings['credentials'][0]['models'].append('ocr.ovisocr3')
    settings.bindings_file.write_text(json.dumps(bindings))
    calls = []
    original = httpx.AsyncClient

    def infer(request):
        body = json.loads(request.content)
        assert body['model'] == 'ovisocr3' and body['stream'] is False
        assert body['chat_template_kwargs'] == {'enable_thinking': False}
        image, prompt = body['messages'][0]['content']
        assert image['image_url']['url'].startswith('data:image/png;base64,')
        assert prompt == {'type': 'text', 'text': PLAIN_OCR_PROMPT}
        calls.append(body)
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
