"""A 3-10-3 topology against real PostgreSQL, with held upstream HTTP calls."""
import asyncio
import json
from threading import Event

import httpx
import psycopg
import pytest

from maas_platform.capacity import ready_replica_budget
from maas_platform.capacity_admission import configuration
from maas_platform.capacity_observe import publish
from maas_platform.controller_capacity import local_slots
from maas_platform.sync_ingress import SyncIngress
from maas_platform.worker import Worker
from test_capacity import topology
from test_integration import env, headers, submit


@pytest.mark.parametrize('meminfo', ['MemFree: 2097152 kB\nMemAvailable: 16777216 kB', None])
def test_local_slots_reports_memory_or_descriptor_limit(monkeypatch, meminfo):
    from maas_platform import controller_capacity as controller

    mib = 1024**2
    monkeypatch.setattr(controller.os, 'sysconf', lambda name: {
        'SC_AVPHYS_PAGES': 524288 if meminfo else 4194304, 'SC_PAGE_SIZE': 4096}[name])
    monkeypatch.setattr(controller.resource, 'getrlimit', lambda _: (4096, 4096))

    def memory(path):
        if path.name == 'meminfo' and meminfo:
            return meminfo
        if path.name == 'memory.max':
            return str(8192 * mib)
        if path.name == 'memory.current':
            return str(4096 * mib)
        raise FileNotFoundError

    monkeypatch.setattr(controller.Path, 'read_text', memory)
    assert local_slots(128 * mib, 1) == (8, 'available_memory')
    monkeypatch.setattr(controller.resource, 'getrlimit', lambda _: (80, 80))
    assert local_slots(128 * mib, 1) == (4, 'file_descriptors')
    monkeypatch.setattr(controller.resource, 'getrlimit', lambda _: (64, 64))
    assert local_slots(128 * mib, 1) == (0, 'file_descriptors')


def test_automatic_capacity_scales_actual_dispatch_without_reconfiguration(env, tmp_path, monkeypatch):
    client, service, settings, auth, _ = env
    raw, catalog, native = topology(10)
    workers, models, instances, routes, targets = native
    names = list(catalog)[:3]
    a, b, c = names
    raw.pop('global_limit')
    raw['pools'] = {name: raw['pools'][name] for name in names}
    raw['shared_gpu_vram_budgets'] = {'gpu-type': 15}
    catalog = {name: catalog[name] for name in names}
    for name, model in catalog.items():
        model.update(driver='gpustack.ocr', revision='test_agent_capacity', max_runtime_s=60)
        pool = raw['pools'][name]
        pool.pop('hard_limit')
        pool['shared_gpu_vram_bytes'] = 3
    for rows in (models, routes, targets):
        for item in range(4, 11):
            del rows[item]
    instances.clear()
    for num, worker in workers.items():
        worker['unreachable'] = num > 3
        worker['system_reserved'] = {'vram': 1}
        worker['status']['gpu_devices'][0]['memory'] = {'total': 16}
        placements = (1, 3) if num == 2 else (1, 1, 2) if num <= 3 else (1, 2, 3)
        for model_id in placements:
            ident = len(instances) + 1
            instances[ident] = {'id': ident, 'model_id': model_id, 'worker_id': num, 'gpu_indexes': [0],
                                'state': 'running', 'computed_resource_claim': {'vram': {'0': 3}}}

    settings.max_running = settings.max_project_running = settings.executor_slots = 8
    settings.capacity_policy_file = tmp_path / 'automatic.json'
    settings.capacity_policy_file.write_text(json.dumps(raw))
    settings.capacity_policy_file.chmod(0o600)
    settings.catalog_file.write_text(json.dumps(catalog))
    bindings = json.loads(settings.bindings_file.read_text())
    bindings['credentials'][0].update(project='test_agent_capacity', models=names)
    settings.bindings_file.write_text(json.dumps(bindings))
    identity = auth.authenticate('gpustack_registered_a')
    policy, _, revision = configuration(settings)

    async def observe(expected):
        for model_id, model in models.items():
            model['ready_replicas'] = sum(i['model_id'] == model_id and not workers[i['worker_id']]['unreachable']
                                          for i in instances.values())
        budget = ready_replica_budget(policy, models)
        assert budget['global']['total'] == expected
        async with await psycopg.AsyncConnection.connect(settings.database_url) as db:
            clock = await (await db.execute('SELECT clock_timestamp()')).fetchone()
            assert await publish(db, revision, clock[0], budget)
        return budget

    async def exercise():
        low = await observe(8)
        assert low['global'] == {'total': 8, 'sync_reserved': 2, 'async_limit': 6}
        service.pause(True)
        assert service.set_capacity_mode(True) == 'enforce'
        service.pause(False)

        upload = client.post('/maas/v1/artifacts', headers={**headers(), 'Content-Type': 'image/png'},
                             content=b'\x89PNG\r\n\x1a\nfixture')
        assert upload.status_code == 201
        for i, model in enumerate([a] * 13 + [b] * 9 + [c] * 4):
            accepted = submit(client, 'test_agent_tide_' + str(i), model=model, max_attempts=1,
                              input={'image': {'artifact_id': upload.json()['id']}})
            assert accepted.status_code == 202

        released = asyncio.Event()
        first, peak, resumed = asyncio.Event(), asyncio.Event(), asyncio.Event()
        sent = []
        finished = {'text': 'ok', 'lines': [], 'elapsed_ms': 1, 'node': 'test_agent_backend'}
        reply = {'choices': [{'message': {'content': json.dumps(finished)}}]}
        actual_client = httpx.AsyncClient
        stop = Event()

        async def upstream(request):
            sent.append(json.loads(request.content)['model'])
            event = {6: first, 21: peak, 22: resumed}.get(len(sent))
            if event:
                event.set()
            await released.wait()
            return httpx.Response(200, json=reply)

        monkeypatch.setattr('maas_platform.worker.local_slots', lambda *_: (32, 'available_memory'))
        monkeypatch.setattr('maas_platform.ocr_connector.httpx.AsyncClient',
                            lambda **kw: actual_client(transport=httpx.MockTransport(upstream), **kw))
        sync_sent = []

        def sync_upstream(request):
            sync_sent.append(json.loads(request.content)['model'])
            return httpx.Response(200, json=reply)

        monkeypatch.setattr('maas_platform.sync_ingress.local_slots', lambda *_: (1, 'available_memory'))
        ingress = SyncIngress(service, lambda **kw: actual_client(transport=httpx.MockTransport(sync_upstream), **kw))
        client.app.state.sync_ingress = ingress

        def sync(model):
            return client.post('/gpustack/v1/chat/completions', headers=headers(),
                               json={'model': model, 'messages': []})

        running = asyncio.create_task(Worker(service, stop_claiming=stop).serve(settings.executor_slots))
        holds = []
        try:
            await asyncio.wait_for(first.wait(), 12)
            assert service.claim() is None
            for worker in workers.values():
                worker['unreachable'] = False
            high = await observe(29)
            assert high['global'] == {'total': 29, 'sync_reserved': 8, 'async_limit': 21}
            assert [high['pools'][name]['total'] for name in names] == [12, 9, 8]
            await asyncio.wait_for(peak.wait(), 15)
            assert sent.count('upstream-1') == 12 and sent.count('upstream-2') == 9
            assert ingress.request_slots.acquire(blocking=False)
            try:
                assert sync(c).json()['error']['code'] == 'SYNC_CONTROLLER_CAPACITY'
            finally:
                ingress.request_slots.release()
            assert sync(a).status_code == 429  # Its own pool is full.
            holds.extend(ingress.begin(identity, c, 60) for _ in range(7))
            assert sync(c).status_code == 200  # Shared project already has over eight requests.
            holds.append(ingress.begin(identity, c, 60))
            assert sync(c).status_code == 429
            assert sync_sent == ['upstream-3']

            for num, worker in workers.items():
                worker['unreachable'] = num not in {4, 7, 9}
            reduced = await observe(9)
            assert reduced['global'] == {'total': 9, 'sync_reserved': 3, 'async_limit': 6}
            assert [reduced['pools'][name]['total'] for name in names] == [3, 3, 3]
            assert sync(c).status_code == 429 and service.claim() is None and len(sent) == 21
            for token in holds:
                ingress.finish(token, True)
            holds.clear()
            released.set()
            await asyncio.wait_for(resumed.wait(), 12)
        finally:
            stop.set()
            released.set()
            for token in holds:
                ingress.finish(token, True)
            await asyncio.wait_for(running, 15)

    asyncio.run(exercise())
