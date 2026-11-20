from datetime import datetime, timedelta, timezone
import io
import json
from pathlib import Path
import time
from types import SimpleNamespace
from unittest.mock import Mock
import httpx
import pytest
from PIL import Image
from pydantic import ValidationError
from maas_platform.document_ocr import DocumentOCRAdapter, GUARDED_RUNTIME_DIGEST
from maas_platform.document_output import parse_document_output
from maas_platform.errors import ServiceError

pytest.importorskip('pypdfium2', reason='Install requirements-document.txt for document tests')


def model():
    return json.loads(Path('deploy/maas/catalog.document.example.json').read_text())['document.unlimited-ocr-2']


def page():
    return {'number': 1, 'width': 100, 'height': 100, 'source_width': 100,
            'source_height': 100, 'source_unit': 'pixel', 'orientation_normalized': True}


def result(text, count=1, **envelope):
    return {'envelope': {'choices': [{'finish_reason': 'stop', 'message': {'content': text}}], **envelope},
            'pages': [{**page(), 'number': i + 1} for i in range(count)],
            'provenance': {}, 'parameters': {'dpi': 150}}


def test_layout_pages_cross_table_and_disabled_image_files():
    text = ('<PAGE><BLOCK>cross_table[0,0,1000,500]<FORK><table><tr><th>Code</th></tr><tr><td>A</td></tr></table>'
            '<BLOCK>image[100,500,600,800]<FORK>'
            '<PAGE><BLOCK>append_table[0,0,1000,500]<FORK><table><tr><th>Code</th></tr><tr><td>B</td></tr></table>')
    output = parse_document_output(result(text, 2))
    assert output['page_count'] == 2 and output['raw_output'] == text
    assert '<td>A</td>' in output['markdown'] and '<td>B</td>' in output['markdown']
    assert output['markdown'].count('<th>Code</th>') == 1
    assert 'page1_' not in output['markdown'] and output['image_extraction'] is False
    assert output['pages'][0]['blocks'][0]['bbox'] == [0, 0, 1000, 500]
    assert all('confidence' not in b for p in output['pages'] for b in p['blocks'])


@pytest.mark.parametrize('value', [result('missing page'), result('<PAGE>x', 2),
    result('<PAGE><BLOCK>text[0,0,1001,500]<FORK>x'),
    result('<PAGE>x' * 4, 4),
    result('<PAGE>', metadata={'doc_split_batches': -1}),
    result('<PAGE>', metadata={'doc_split_batches': True}),
    result('<PAGE>x' * 4, 4, metadata={'doc_split_complete': False})])
def test_reject_missing_pages_bad_coordinates_and_incomplete_windows(value):
    with pytest.raises(ServiceError) as error:
        parse_document_output(value)
    assert error.value.code == 'OUTPUT_INVALID'


def test_complete_windows_and_blank_pages():
    output = parse_document_output(result('<PAGE>' * 4, 4,
        metadata={'doc_split_complete': True, 'doc_split_batches': 2}))
    assert output['page_count'] == 4 and output['window_count'] == 2
    assert all(p['blocks'] == [] for p in output['pages'])


def context(data):
    service = SimpleNamespace(settings=SimpleNamespace(gpustack_url='http://gateway.test', max_output_bytes=20 * 1024**2),
        auth=SimpleNamespace(key_for=lambda _: 'fixture-key'), begin_backend=Mock(return_value=None), complete_backend=Mock())
    task = {'parameters': {'dpi': 150}, 'deadline': datetime.now(timezone.utc) + timedelta(seconds=60)}
    return SimpleNamespace(task=task, service=service, model=model(), identity=None, deadline=time.monotonic() + 30,
        check=lambda: None, read_input=lambda: (data, 'image/png'))


def png():
    output = io.BytesIO();Image.new('RGB', (100, 100), 'white').save(output, format='PNG')
    return output.getvalue()


def test_real_preprocess_single_send_and_page_count(monkeypatch):
    factory, calls = httpx.AsyncClient, []
    def reply(request):
        body = json.loads(request.content);calls.append(body)
        assert body['stream'] is False and body['custom_logit_processor'] == 'maas.unlimited-ocr-2.ngram.v1'
        assert len(body['messages'][0]['content']) == 2
        return httpx.Response(200, json=result('<PAGE><BLOCK>text[0,0,1000,500]<FORK>hello')['envelope'])
    monkeypatch.setattr('maas_platform.ocr_connector.httpx.AsyncClient',
        lambda **kw: factory(transport=httpx.MockTransport(reply), **kw))
    ctx = context(png())
    output = DocumentOCRAdapter().execute(ctx)
    assert len(calls) == 1 and output['pages'][0]['markdown'] == 'hello'
    assert ctx.metering_pages == 1 and ctx.metering_tokens['reason'] == 'DOCUMENT_WINDOW_USAGE_UNVERIFIED'
    ctx.service.begin_backend.assert_called_once_with(ctx.task)
    ctx.service.complete_backend.assert_called_once_with(ctx.task)


def test_ambiguous_failure_not_replayed_or_completed(monkeypatch):
    factory, calls = httpx.AsyncClient, []
    def reply(request):
        calls.append(request)
        return httpx.Response(503)
    monkeypatch.setattr('maas_platform.ocr_connector.httpx.AsyncClient',
        lambda **kw: factory(transport=httpx.MockTransport(reply), **kw))
    ctx = context(png())
    with pytest.raises(ServiceError) as error:
        DocumentOCRAdapter().execute(ctx)
    assert error.value.code == 'UNKNOWN_BACKEND_ACTIVITY' and len(calls) == 1
    ctx.service.complete_backend.assert_not_called()


def test_untrusted_options_and_catalog_drift_rejected():
    adapter = DocumentOCRAdapter()
    with pytest.raises(ValidationError):
        adapter.validate({'document': {'artifact_id': 'art_x'}}, {'custom_logit_processor': 'external'})
    with pytest.raises(ValueError):
        adapter.validate_model({**model(), 'runtime_image_digest': 'different'})
    adapter.validate_model(model())


def test_guarded_catalog_accepts_guarded_image_and_legacy_mount():
    adapter = DocumentOCRAdapter()
    derived = {**model(), 'runtime_image_digest': GUARDED_RUNTIME_DIGEST}
    with pytest.raises(ValueError):
        adapter.validate_model(derived)
    adapter.validate_model({**derived, 'execution_guard': True})
    adapter.validate_model({**model(), 'execution_guard': True})
