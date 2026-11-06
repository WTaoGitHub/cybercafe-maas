from datetime import date
from uuid import uuid4

from test_integration import env
from maas_platform import gpu_daily
from maas_platform.gpu_daily_watch import cards_for, persist, seal_pending


def test_daily_report_is_shared_by_pull_and_retry(env, monkeypatch, tmp_path):
    from fastapi.testclient import TestClient
    from maas_platform.api import create_app

    _, service, settings, auth, _ = env
    key = tmp_path / 'daily-key'
    key.write_text('daily-test-key')
    monkeypatch.setenv('E17_DAILY_QUERY_KEY_FILE', str(key))
    day = date(2026, 11, 5)
    start, _ = gpu_daily.bounds(day)
    model = {'logical_model_name': 'ocr.default', 'gpustack_deployment_id': '18',
             'gpustack_deployment_name': 'ocr-native'}
    worker = {'id': 1, 'state': 'ready', 'status': {'gpu_devices': [{'index': 0, 'uuid': 'GPU-a'}]}}
    replica = {'state': 'running', 'worker_id': 1, 'gpu_indexes': [0],
               'model_id': 18, 'model_name': 'ocr-native'}
    cards = cards_for({1: worker}, {1: replica, 2: replica}, {'ocr-native': ['ocr.default']})
    assert cards == {'GPU-a': [model]}
    session = uuid4()
    persist(service.db, start - 15, session, cards)
    persist(service.db, start + 15, session, {})
    persist(service.db, start + 45, session, {})
    seal_pending(service.db, day)
    path = '/maas/v1/gpu-daily-reports/2026-11-05'
    with TestClient(create_app(settings, auth)) as client:
        assert client.get(path).status_code == 401
        response = client.get(path, headers={'Authorization': 'Bearer daily-test-key'})
        assert response.status_code == 200
        payload = response.json()
    assert payload['records'] == [{
        'gpu_uuid': 'GPU-a', 'online_from': '2026-11-05T00:00:00+08:00',
        'online_until': '2026-11-05T00:00:15+08:00', 'models': [model],
    }]
    with service.db.transaction() as conn:
        previous = conn.execute('SELECT payload FROM e17.daily_reports WHERE day < %s', (day,)).fetchone()
        assert previous['payload']['records'][0]['online_until'] == payload['records'][0]['online_from']
        conn.execute('UPDATE e17.daily_reports SET delivered_at=now() WHERE day < %s', (day,))
    attempts = []

    def post(url, **kwargs):
        attempts.append(kwargs)
        return gpu_daily.httpx.Response(503 if len(attempts) == 1 else 204)

    monkeypatch.setattr(gpu_daily.httpx, 'post', post)
    for _ in range(2):
        gpu_daily.deliver(service.db, 'https://consumer.example/reports', 'push-key')
        with service.db.transaction() as conn:
            conn.execute('UPDATE e17.daily_reports SET next_attempt=now()')
    assert attempts[0] == attempts[1]
    assert attempts[0]['json'] == payload
    assert attempts[0]['headers']['Idempotency-Key'] == payload['report_id']
    with service.db.transaction() as conn:
        assert conn.execute('SELECT delivered_at FROM e17.daily_reports WHERE day=%s', (day,)).fetchone()['delivered_at']
