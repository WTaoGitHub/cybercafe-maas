"""Exercise configuration-only onboarding with the GPUStack HTTP contract and real PostgreSQL."""
import asyncio
import json
from uuid import uuid4

import httpx
import psycopg
from psycopg.rows import dict_row
import pytest

from maas_platform.capacity import Capacity
from maas_platform.capacity_admission import configuration
from maas_platform.capacity_observe import RESOURCES, publish, read_snapshot
from maas_platform.errors import ServiceError
from maas_platform.worker import Worker
from test_capacity import evaluate, topology
from test_integration import env, headers


@pytest.mark.parametrize('pool_count', [2, 5, 11])
def test_configured_models_and_ready_replicas_control_actual_dispatch(env, tmp_path, monkeypatch, pool_count):
    client, service, settings, auth, _ = env
    raw, catalog, native = topology(pool_count)
    names = list(catalog)
    first, *added = names
    for model in catalog.values():
        model.update(driver='gpustack.ocr', revision='test-agent-r1', max_runtime_s=30)
    raw['global_limit'] = settings.max_running = settings.max_project_running = pool_count + 1
    settings.capacity_policy_file = tmp_path / 'capacity-policy.json'
    settings.capacity_policy_file.touch(mode=0o600)
    bindings = json.loads(settings.bindings_file.read_text())
    bindings['credentials'][0].update(project='test_agent_onboarding', models=names)
    settings.bindings_file.write_text(json.dumps(bindings))
    ingress = client.app.state.sync_ingress
    identity = auth.authenticate('gpustack_registered_a')
    worker = Worker(service, executor_id='test_agent_onboarding')
    forwarded = []

    def upstream(request):
        forwarded.append(json.loads(request.content)['model'])
        value = {'text': 'ok', 'lines': [], 'elapsed_ms': 1, 'node': 'test_agent_backend'}
        return httpx.Response(200, json={'choices': [{'message': {'content': json.dumps(value)}}]})

    def inference_client(**kwargs):
        return httpx.AsyncClient(transport=httpx.MockTransport(upstream), **kwargs)

    ingress.client_factory = inference_client
    monkeypatch.setattr('maas_platform.ocr_connector.inference_client', inference_client)
    uploaded = client.post('/maas/v1/artifacts', headers={**headers(), 'Content-Type': 'image/png'},
                           content=b'\x89PNG\r\n\x1a\nfixture')
    assert uploaded.status_code == 201

    def submit(model):
        return client.post('/maas/v1/tasks', headers=headers(idem='test_agent_' + uuid4().hex), json={
            'model': model, 'max_attempts': 1, 'input': {'image': {'artifact_id': uploaded.json()['id']}}})

    def sync(model):
        return client.post('/gpustack/v1/chat/completions', headers=headers(),
                           json={'model': model, 'messages': []})

    def topology_endpoint(request):
        kind = request.url.path.rsplit('/', 1)[-1]
        rows = native[RESOURCES.index(kind)]
        return httpx.Response(200, json={'items': list(rows.values()),
                                         'pagination': {'totalPage': 1, 'total': len(rows)}})

    def load_config(configured):
        service.pause(True)
        selected = {name: catalog[name] for name in configured}
        settings.catalog_file.write_text(json.dumps(selected))
        settings.capacity_policy_file.write_text(json.dumps({
            **raw, 'pools': {name: raw['pools'][name] for name in configured}}))
        policy, _, revision = configuration(settings)
        capacity = Capacity(policy, settle=5, ttl=30)

        def observe(at):
            async def read_and_publish():
                async with httpx.AsyncClient(base_url='http://gpustack.test/v2/',
                                             transport=httpx.MockTransport(topology_endpoint)) as api:
                    snapshot = await read_snapshot(api)
                budget = evaluate(capacity, policy, selected, snapshot, at)
                async with await psycopg.AsyncConnection.connect(settings.database_url, row_factory=dict_row) as db:
                    observed = (await (await db.execute('SELECT clock_timestamp() AS at')).fetchone())['at']
                    assert await publish(db, revision, observed, budget)
                return budget

            return asyncio.run(read_and_publish())

        with pytest.raises(ServiceError) as missing:
            service.set_capacity_mode(True)
        assert missing.value.code == 'CAPACITY_UNAVAILABLE'
        assert observe(1)['global']['total'] == 0
        assert observe(6)['global']['total'] == len(configured)
        assert service.set_capacity_mode(True) == 'enforce'
        service.pause(False)
        return observe

    observe = load_config([first])
    pending = submit(first)
    assert pending.status_code == 202, pending.json()
    assert not worker.run_once() and not forwarded
    assert sync(first).status_code == 200

    # The desired replica count stays unchanged throughout the readiness transitions.
    new_id = pool_count + 1
    workers, _, instances, _, _ = native
    workers[new_id] = {**workers[1], 'id': new_id,
                       'status': {'gpu_devices': [{'uuid': 'extra-gpu', 'name': 'gpu-type', 'index': 0}]}}
    instances[new_id] = {**instances[1], 'id': new_id, 'worker_id': new_id}
    assert observe(7)['global']['total'] == 1
    enlarged = observe(12)
    assert enlarged['global'] == {'total': 2, 'sync_reserved': 1, 'async_limit': 1}
    assert enlarged['pools'][first]['ready_ids'] == [1, new_id]
    assert worker.run_once() and forwarded == ['upstream-1'] * 2
    assert service.get(identity, pending.json()['id'])['state'] == 'SUCCEEDED'

    sync_holds = [ingress.begin(identity, first, 30) for _ in range(2)]
    workers[new_id]['unreachable'] = True
    assert observe(13)['global']['total'] == 1
    assert sync(first).status_code == 429
    assert submit(first).status_code == 202 and not worker.run_once()
    for token in sync_holds:
        ingress.finish(token, True)
    workers[1]['unreachable'] = True
    assert observe(14)['global']['total'] == 0
    assert sync(first).json()['error']['code'] == 'CAPACITY_UNAVAILABLE'
    assert not worker.run_once() and forwarded == ['upstream-1'] * 2
    workers[1]['unreachable'] = False
    assert observe(15)['global']['total'] == 0
    assert observe(20)['global']['total'] == 1

    load_config(names)
    assert worker.run_once()  # The queue stayed intact while the earlier budget was too small.
    for name in added:
        assert sync(name).status_code == 200
        result = submit(name)
        assert result.status_code == 202 and worker.run_once()
        assert service.get(identity, result.json()['id'])['state'] == 'SUCCEEDED'
        assert forwarded[-2:] == [catalog[name]['upstream_model']] * 2

    observe = load_config(added)
    assert set(observe(7)['pools']) == set(added)
    assert sync(first).status_code == submit(first).status_code == 404
    assert sync(added[0]).status_code == 200
    assert service.metrics()['successful_tasks'] == len(added) + 2
