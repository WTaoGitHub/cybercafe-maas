"""Run with MAAS_TEST_DATABASE_URL pointing to a disposable database ending in _test."""
import asyncio
import json
import threading

import httpx

from maas_platform.worker import Worker
from test_integration import env, headers, sync_setup


def submit_image(client, key, name):
    uploaded = client.post('/maas/v1/artifacts', headers={**headers(key), 'Content-Type': 'image/png'},
                           content=b'\x89PNG\r\n\x1a\nfixture')
    assert uploaded.status_code == 201
    accepted = client.post('/maas/v1/tasks', headers=headers(key, 'test_agent_' + name),
                           json={'model': 'ocr.default', 'max_attempts': 1,
                                 'input': {'image': {'artifact_id': uploaded.json()['id']}}})
    assert accepted.status_code == 202
    return accepted.json()['id']


def test_single_process_sends_immediately_reuses_client_and_drains(env, monkeypatch):
    client, service, settings = sync_setup(env, monkeypatch, None)
    settings.max_running = 4
    settings.max_project_running = 2
    settings.heartbeat_seconds = .05
    bindings = json.loads(settings.bindings_file.read_text())
    bindings['credentials'][1]['models'].append('ocr.default')
    settings.bindings_file.write_text(json.dumps(bindings))
    stop = threading.Event()
    renewed = threading.Event()
    batches = service.heartbeat_many

    def record_renewal(tasks):
        count = batches(tasks)
        if count == 2:
            renewed.set()
        return count

    monkeypatch.setattr(service, 'heartbeat_many', record_renewal)
    original = httpx.AsyncClient
    clients, calls = [], []

    async def exercise():
        reached = [asyncio.Event() for _ in range(3)]
        release_first_and_third = asyncio.Event()
        release_second = asyncio.Event()

        async def transport(request):
            assert 'cookie' not in request.headers
            call = len(calls)
            calls.append(request.headers['authorization'])
            reached[call].set()
            await (release_second if call == 1 else release_first_and_third).wait()
            value = {'text': 'ok', 'lines': [], 'elapsed_ms': 1, 'node': 'test_agent_backend'}
            return httpx.Response(200, headers={'Set-Cookie': 'session=unsupported; Path=/'},
                                  json={'choices': [{'message': {'content': json.dumps(value)}}]})

        def factory(**kwargs):
            result = original(transport=httpx.MockTransport(transport), **kwargs)
            clients.append(result)
            return result

        monkeypatch.setattr('maas_platform.ocr_connector.httpx.AsyncClient', factory)
        first = await asyncio.to_thread(submit_image, client, 'a', 'first')
        running = asyncio.create_task(Worker(service, stop_claiming=stop).serve(2))
        try:
            await asyncio.wait_for(reached[0].wait(), 4)
            second = await asyncio.to_thread(submit_image, client, 'b', 'second')
            third = await asyncio.to_thread(submit_image, client, 'b', 'third')
            await asyncio.wait_for(reached[1].wait(), 4)
            assert await asyncio.to_thread(renewed.wait, 3)
            assert len(calls) == 2
            release_second.set()
            await asyncio.wait_for(reached[2].wait(), 4)
            fourth = await asyncio.to_thread(submit_image, client, 'a', 'queued')
        finally:
            stop.set()
            release_second.set()
            release_first_and_third.set()
            await asyncio.wait_for(running, 5)
        return first, second, third, fourth

    first, second, third, fourth = asyncio.run(exercise())
    assert len(clients) == 1 and clients[0].is_closed
    assert calls == ['Bearer gpustack_registered_a', 'Bearer gpustack_registered_b', 'Bearer gpustack_registered_b']
    with service.db.transaction() as connection:
        tasks = connection.execute('SELECT id,state FROM tasks').fetchall()
        assert {item['id']: item['state'] for item in tasks} == {
            first: 'SUCCEEDED', second: 'SUCCEEDED', third: 'SUCCEEDED', fourth: 'QUEUED'}
        assert connection.execute('SELECT count(*) n FROM task_usage_events').fetchone()['n'] == 3
        assert connection.execute('SELECT count(*) n FROM ocr_usage_records').fetchone()['n'] == 3


def test_drain_after_input_preparation_defers_without_sending(env, monkeypatch):
    client, service, _ = sync_setup(env, monkeypatch, None)
    task_id = submit_image(client, 'a', 'drain')
    stop = threading.Event()
    read = service.read_task_input

    def prepare(task):
        result = read(task)
        stop.set()
        return result

    def unexpected_client(**kwargs):
        raise AssertionError('Inference client opened after draining')

    monkeypatch.setattr(service, 'read_task_input', prepare)
    monkeypatch.setattr('maas_platform.ocr_connector.httpx.AsyncClient', unexpected_client)
    assert Worker(service, stop_claiming=stop).run_once()
    with service.db.transaction() as connection:
        attempt = connection.execute('SELECT state,error_code FROM task_attempts WHERE task_id=%s', (task_id,)).fetchone()
        assert attempt == {'state': 'DEFERRED', 'error_code': 'EXECUTOR_DRAINING'}
        assert connection.execute('SELECT count(*) n FROM backend_activity').fetchone()['n'] == 0
        assert connection.execute('SELECT state FROM tasks WHERE id=%s', (task_id,)).fetchone()['state'] == 'QUEUED'
