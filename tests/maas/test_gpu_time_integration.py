from datetime import datetime

from maas_platform.gpu_time import BEIJING, report, save
from test_integration import env


def test_postgres_daily_and_monthly_reports(env):
    _, service, *_ = env
    start = int(datetime(2026, 11, 1, tzinfo=BEIJING).timestamp())
    session = '3ae80fae-a100-4fa4-a799-5fab9dd0d7c4'
    for offset, cards in [(-30, {'gpu-a': True}), (30, {'gpu-a': False}), (90, None)]:
        save(service.db, start + offset, session, cards)
    daily = report(service.db, '2026-11-01', start + 120)
    monthly = report(service.db, '2026-11', start + 120)
    assert daily['gpus'] == monthly['gpus'] == {
        'gpu-a': {'online_seconds': 30, 'not_ready_seconds': 0, 'unknown_seconds': 90},
    }


def test_operator_web_and_json_use_same_report(env, monkeypatch, tmp_path):
    from fastapi.testclient import TestClient
    from maas_platform.api import create_app
    from test_integration import headers

    disabled, service, settings, auth, _ = env
    path = '/maas/ops/gpu-time'
    assert disabled.get(path).status_code == 404
    secret = tmp_path / 'operator-key'
    secret.write_text('test-secret')
    monkeypatch.setenv('E17_QUERY_KEY_FILE', str(secret))
    now = int(datetime.now(BEIJING).timestamp())
    period = datetime.now(BEIJING).date().isoformat()
    session = '3ae80fae-a100-4fa4-a799-5fab9dd0d7c4'
    save(service.db, now - 60, session, {'gpu-test': True, '<gpu-other>': False})
    save(service.db, now - 1, session, {'gpu-test': True, '<gpu-other>': False})
    with TestClient(create_app(settings, auth)) as client:
        assert client.get(path, headers=headers()).status_code == 401
        assert client.get(path + '/report', auth=('e17', 'wrong')).status_code == 401
        operator = ('e17', 'test-secret')
        response = client.get(path + '/report', auth=operator,
                              params={'period': period, 'gpu_uuid': 'gpu-test'})
        assert response.status_code == 200 and response.headers['cache-control'] == 'no-store'
        assert response.json()['last_success_at'] == now - 1
        assert list(response.json()['gpus']) == ['gpu-test']
        page = client.get(path, auth=operator, params={'period': period})
        assert page.status_code == 200 and '&lt;gpu-other&gt;' in page.text
        assert 'gpu-test' in page.text
        assert client.get(path, auth=operator, params={'period': 'invalid'}).status_code == 422
