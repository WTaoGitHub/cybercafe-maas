import pytest
from maas_platform.metering import tokens_from_response,receipt,single_image_ocr


@pytest.mark.parametrize('usage',[None,{}, {'prompt_tokens':1}, {'prompt_tokens':True,'completion_tokens':1}, {'prompt_tokens':-1,'completion_tokens':1}, {'prompt_tokens':1.2,'completion_tokens':2}, {'prompt_tokens':1,'completion_tokens':2,'total_tokens':4}])
def test_no_estimated_tokens(usage):
    tokens=tokens_from_response({'usage':usage})
    assert tokens['status']=='unavailable' and tokens['input_tokens'] is tokens['output_tokens'] is None


@pytest.mark.parametrize('a,b',[(0,0),(10,20)])
def test_actual_usage_and_stable_id(a,b):
    tokens=tokens_from_response({'usage':{'prompt_tokens':a,'completion_tokens':b,'total_tokens':a+b}})
    assert tokens['status']=='available' and tokens['input_tokens']==a and tokens['output_tokens']==b
    r=receipt('task','task_x','ocr.default','r1',tokens)
    assert r['id']=='usage_task_task_x' and r['successful_images']==1
    assert r['id']==receipt('task','task_x','ocr.default','r1')['id']


def test_unverified_chat_is_not_a_successful_image():
    assert not single_image_ocr({'messages':[]},{'choices':[]})



@pytest.mark.parametrize('path', [
    '/maas/v1/usage/usage_task_task_x', '/maas/v1/tasks/task_x/usage', '/maas/v1/tasks/task_x',
])
@pytest.mark.parametrize('reported', [None, {'prompt_tokens': 0, 'completion_tokens': 22}])
def test_usage_type_api(path, reported, monkeypatch, tmp_path):
    from contextlib import nullcontext
    from types import SimpleNamespace
    from unittest.mock import Mock
    from fastapi.testclient import TestClient
    from maas_platform.api import create_app
    from maas_platform.config import Settings

    value = receipt('task', 'task_x', 'ocr.default', 'r1', tokens_from_response({'usage': reported}))
    auth = Mock()
    auth.authenticate.return_value = SimpleNamespace(project='project')
    app = create_app(Settings("unused", tmp_path, "http://localhost", tmp_path, tmp_path, tmp_path), auth)
    connection = Mock()
    connection.execute.return_value.fetchone.return_value = {'id': 'task_x', 'receipt': value}
    monkeypatch.setattr(app.state.service.db, 'transaction', lambda: nullcontext(connection))
    detail = path == '/maas/v1/tasks/task_x'
    task = {
        'id': 'task_x', 'model': 'ocr.default', 'revision': 'r1', 'attempts': 1,
        'state': 'SUCCEEDED', 'error_code': None, 'result_artifact_id': 'art_x',
        **dict.fromkeys(('deadline', 'created_at', 'updated_at'), value['recorded_at']),
        'usage': value,
    }
    connection.execute.return_value.fetchone.return_value.update(task)
    monkeypatch.setattr(app.state.service, 'artifact', lambda *_: (b'{"outputs":{"text":"OCR"}}', None))
    client = TestClient(app)
    original = client.get(path).json()
    assert original == ({**original, 'usage': {'successful_images': 1}} if detail else value)

    def expected(usage):
        return {**original, 'usage': usage} if detail else usage

    images = client.get(path, params={'usage_type': 'images'})
    assert images.status_code == 200 and images.json() == expected({'successful_images': 1})
    if detail:
        assert images.json()['status'] == 'SUCCEEDED'
        assert images.json()['result'] == {'text': 'OCR'}
    else:
        assert images.headers['cache-control'] == 'no-store'

    tokens = client.get(path, params={'usage_type': 'tokens'})
    if reported is None:
        assert tokens.status_code == 422
        assert tokens.json()['error']['code'] == 'USAGE_TYPE_UNSUPPORTED'
    else:
        assert tokens.status_code == 200
        assert tokens.json() == expected({'input_tokens': 0, 'output_tokens': 22})

    invalid = client.get(path, params={'usage_type': 'other'})
    assert invalid.status_code == 422
    assert invalid.json()['error']['code'] == 'INPUT_INVALID'

    if detail:
        connection.execute.return_value.fetchone.return_value.update(state='QUEUED', usage=None)
        pending = client.get(path, params={'usage_type': 'tokens'})
        assert pending.status_code == 200
        assert pending.json()['status'] == 'QUEUED' and pending.json()['usage'] is None
