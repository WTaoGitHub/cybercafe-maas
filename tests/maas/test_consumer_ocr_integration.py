import base64
import pytest
from test_integration import env,headers,sync_setup
from test_consumer_ocr import png


def body(**kw):return {'model':'ocr.default','image_base64':base64.b64encode(png()).decode(),**kw}

def test_legacy_ocr_route_is_removed(env, monkeypatch):
    client, _, _ = sync_setup(env, monkeypatch, lambda _: pytest.fail('Removed route must not run OCR'))
    assert client.post('/ocr', headers=headers(), json=body()).status_code == 404


@pytest.mark.parametrize('field,value', [
    ('image_base64', base64.b64encode(png()).decode()),
    ('image_url', 'https://example.org/image.png'),
])
def test_v1_ocr_shares_task_submission_and_idempotency(env, monkeypatch, field, value):
    client, service, _ = sync_setup(env, monkeypatch, lambda _: pytest.fail('Submission must not run OCR'))
    async def fetch(*args):
        return png()
    monkeypatch.setattr('maas_platform.task_image_url.fetch_image', fetch)
    payload = {'model': 'ocr.default', field: value}
    key = headers(idem='ocr-alias')
    created = client.post('/v1/ocr', headers=key, json=payload)
    assert created.status_code == 202
    assert created.headers['location'] == '/maas/v1/tasks/' + created.json()['id']
    replay = client.post('/maas/v1/tasks', headers=key, json={
        'model': 'ocr.default', 'input': {'image': {field: value}},
    })
    assert replay.status_code == 200 and replay.json() == created.json()
    with service.db.transaction() as conn:
        assert conn.execute('SELECT count(*) n FROM tasks').fetchone()['n'] == 1


@pytest.mark.parametrize('return_boxes', [False, True])
def test_v1_ocr_return_boxes_applies_to_downloaded_result(env, monkeypatch, return_boxes):
    from maas_platform.worker import Worker
    from test_metering_integration import configured

    client, service, _ = configured(env, monkeypatch)
    payload = body(return_boxes=return_boxes)
    key = headers(idem='ocr-result-shape')
    created = client.post('/v1/ocr', headers=key, json=payload)
    assert created.status_code == 202
    assert client.post('/v1/ocr', headers=key, json={
        **payload, 'return_boxes': not return_boxes,
    }).status_code == 409
    replay = client.post('/maas/v1/tasks', headers=key, json={
        'model': payload['model'],
        'input': {'image': {'image_base64': payload['image_base64']}},
        'parameters': {} if return_boxes else {'return_boxes': False},
    })
    assert replay.status_code == 200
    assert Worker(service).run_once()
    task = client.get(created.headers['location'], headers=headers()).json()
    assert task['status'] == 'SUCCEEDED'
    response = client.get('/maas/v1/artifacts/' + task['result_artifact_id'], headers=headers())
    assert response.status_code == 200
    line, = response.json()['outputs']['lines']
    assert line['text'] == 'hello'
    assert line['score'] == 0.9
    assert ('box' in line) == return_boxes

def test_v1_ocr_omitted_idempotency_key_creates_independent_tasks(env, monkeypatch):
    client, _, _ = sync_setup(env, monkeypatch, lambda _: pytest.fail('Submission must not run OCR'))
    payload = body()
    first = client.post('/v1/ocr', headers=headers(), json=payload)
    second = client.post('/v1/ocr', headers=headers(), json=payload)
    assert first.status_code == second.status_code == 202
    assert first.json()['id'] != second.json()['id']
    standard = {
        'model': payload['model'],
        'input': {'image': {'image_base64': payload['image_base64']}},
    }
    assert client.post('/maas/v1/tasks', headers=headers(), json=standard).status_code == 400


def test_v1_ocr_request_id_fallback_order(env, monkeypatch):
    client, _, _ = sync_setup(env, monkeypatch, lambda _: pytest.fail('Submission must not run OCR'))
    payload = body(request_id='body-id')
    explicit = {**headers(idem='explicit-id'), 'X-Request-ID': 'header-id'}
    fallback = {**headers(), 'X-Request-ID': 'header-id'}
    for request_headers in (explicit, fallback, headers()):
        created = client.post('/v1/ocr', headers=request_headers, json=payload)
        assert created.status_code == 202
        replay = client.post('/v1/ocr', headers=request_headers, json=payload)
        assert replay.status_code == 200
        assert replay.json()['id'] == created.json()['id']
        conflict = client.post('/v1/ocr', headers=request_headers, json={**payload, 'return_boxes': False})
        assert conflict.status_code == 409
