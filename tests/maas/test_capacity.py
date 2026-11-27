import asyncio
from datetime import timedelta
from hashlib import sha256
import json
from threading import Event

import httpx
import psycopg
import pytest
from psycopg.rows import dict_row
from pydantic import ValidationError

from maas_platform.capacity import Capacity, Policy, catalog_routes, limits, policy_hash, ready_candidates
from maas_platform.capacity_observe import RESOURCES, TTL, event_identity, publish, read_snapshot, status
from maas_platform import capacity_observe
from test_integration import env


def topology(count):
    pools, catalog = {}, {}
    workers, models, instances, routes, targets = [{} for _ in RESOURCES]
    for num in range(1, count + 1):
        alias, name = f'arbitrary-model-{num}', f'upstream-{num}'
        pools[alias] = {'cluster_id': 1, 'model_id': num, 'owner_principal_id': 4,
                        'routes': {alias: num}, 'gpu_names': ['gpu-type'], 'slots_per_replica': 1, 'hard_limit': 8,
                        'queue_limit': 25, 'gateway_verified': True}
        catalog[alias] = {'upstream_model': name, 'driver': 'any-kind'}
        workers[num] = {'id': num, 'state': 'ready', 'cluster_id': 1,
                        'status': {'gpu_devices': [{'uuid': f'gpu-{num}', 'name': 'gpu-type', 'index': 0}]}}
        models[num] = {'id': num, 'cluster_id': 1, 'owner_principal_id': 4, 'replicas': 999}
        instances[num] = {'id': num, 'state': 'running', 'model_id': num, 'worker_id': num, 'gpu_indexes': [0]}
        routes[num] = {'id': num, 'name': name, 'owner_principal_id': 4}
        targets[num] = {'id': num, 'route_id': num, 'model_id': num, 'state': 'active', 'weight': 100}
    policy = {'global_limit': 8, 'sync_percent': 25, 'pools': pools}
    return policy, catalog, (workers, models, instances, routes, targets)


def evaluate(observation, policy, catalog, native, at):
    return observation.update(at, *ready_candidates(policy, catalog_routes(policy, catalog), *native))


@pytest.mark.parametrize('count', [1, 2, 4, 12])
def test_configured_models_and_aliases_share_capacity(count):
    raw, catalog, native = topology(count)
    raw['pools']['arbitrary-model-1']['routes']['another-alias'] = 1
    catalog['another-alias'] = catalog['arbitrary-model-1']
    policy = Policy.model_validate(raw)
    observation = Capacity(policy, settle=5, ttl=30)
    assert evaluate(observation, policy, catalog, native, 1)['global']['total'] == 0
    result = evaluate(observation, policy, catalog, native, 6)
    assert len(result['pools']) == count
    assert result['pools']['arbitrary-model-1']['aliases'] == ['another-alias', 'arbitrary-model-1']
    assert result['global'] == limits(min(count, 8), 25)
    raw['pools']['second-binding'] = raw['pools']['arbitrary-model-1']
    with pytest.raises(ValidationError, match='multiple pools'):
        Policy.model_validate(raw)


def test_drop_zero_recover_and_expiry():
    raw, catalog, native = topology(2)
    policy = Policy.model_validate(raw)
    observation = Capacity(policy, settle=5, ttl=30)
    read = lambda at: evaluate(observation, policy, catalog, native, at)
    read(1)
    assert read(6)['global'] == {'total': 2, 'sync_reserved': 1, 'async_limit': 1}
    native[0][1]['unreachable'] = True
    assert read(7)['global'] == {'total': 1, 'sync_reserved': 1, 'async_limit': 0}
    native[0][2]['maintenance'] = {'enabled': True}
    assert read(8)['global']['total'] == 0
    native[0][1]['unreachable'] = False
    assert read(9)['global']['total'] == 0
    observation.interrupted()
    assert read(14)['global']['total'] == 0
    assert read(19)['global']['total'] == 1
    with pytest.raises(ValueError, match='Out-of-order'):
        read(18)
    assert read(49)['global']['total'] == 0
    assert read(54)['global']['total'] == 1


def test_only_verified_routes_and_joint_gpu_layouts_add_capacity():
    raw, catalog, native = topology(2)
    native[2][2]['worker_id'] = 1
    policy = Policy.model_validate(raw)
    assert ready_candidates(policy, catalog_routes(policy, catalog), *native)[0] == {
        'arbitrary-model-1': {}, 'arbitrary-model-2': {}}
    raw['shared_gpu_layouts'] = {'gpu-type': [{'arbitrary-model-1': 1, 'arbitrary-model-2': 1}]}
    policy = Policy.model_validate(raw)
    assert all(ready_candidates(policy, catalog_routes(policy, catalog), *native)[0].values())
    native[4][1]['state'] = 'unavailable'
    native[4][2]['owner_principal_id'] = 5
    native[3][2]['owner_principal_id'] = 5
    candidates, reasons = ready_candidates(policy, catalog_routes(policy, catalog), *native)
    assert not any(candidates.values())
    assert reasons == {'arbitrary-model-1': 'route_unavailable', 'arbitrary-model-2': 'binding_unverified'}


def test_gpu_vram_budgets_follow_three_worker_tide():
    raw, catalog, native = topology(3)
    workers, _, instances, _, _ = native
    gib = 1024**3
    pp_vram = 7 * gib // 2
    raw['shared_gpu_vram_budgets'] = {'gpu-type': 16 * gib}
    footprints = {1: 12 * gib, 2: 6 * gib, 3: pp_vram}
    for model, size in footprints.items():
        raw['pools'][f'arbitrary-model-{model}']['shared_gpu_vram_bytes'] = size
    for w in workers.values():
        w['system_reserved'] = {'vram': gib // 4}
        w['status']['gpu_devices'][0]['memory'] = {'total': 16 * gib}
    for ident, (model, worker) in enumerate(
            [(1, 1), (2, 2), (2, 3), (3, 1), (3, 2), (3, 2), (3, 3), (3, 3)], start=1):
        instances[ident] = {'id': ident, 'model_id': model, 'worker_id': worker,
                            'state': 'running', 'gpu_indexes': [0],
                            'computed_resource_claim': {'vram': {'0': footprints[model]}}}
    policy = Policy.model_validate(raw)
    observation = Capacity(policy, settle=5, ttl=30)
    read = lambda at: evaluate(observation, policy, catalog, native, at)
    assert read(1)['global']['total'] == 0
    high = read(6)
    assert high['global'] == limits(8, 25)
    assert [pool['total'] for pool in high['pools'].values()] == [1, 2, 5]
    workers[1]['unreachable'] = True
    low = read(7)
    assert low['global']['total'] == 6
    assert [pool['total'] for pool in low['pools'].values()] == [0, 2, 4]
    workers[1]['unreachable'] = False
    assert read(8)['global']['total'] == 6
    assert read(13)['global']['total'] == 8


@pytest.mark.parametrize('case', [
    'starting', 'missing_claim', 'missing_profile', 'unmanaged', 'low_physical_vram', 'larger_native_claim',
])
def test_rejected_gpu_blocks_aggregated_routes_even_with_other_healthy_replicas(case):
    raw, catalog, native = topology(3)
    workers, _, instances, _, _ = native
    # The old exact combination cannot override the per-card budget.
    raw['shared_gpu_layouts'] = {'gpu-type': [{'arbitrary-model-1': 1, 'arbitrary-model-2': 1}]}
    raw['shared_gpu_vram_budgets'] = {'gpu-type': 12}
    for pool in raw['pools'].values():
        pool['shared_gpu_vram_bytes'] = 5
    for w in workers.values():
        w['system_reserved'] = {'vram': 2}
        w['status']['gpu_devices'][0]['memory'] = {'total': 16}
    for instance in instances.values():
        instance['computed_resource_claim'] = {'vram': {'0': 5}}
    instances[2]['worker_id'] = 1
    instances[4] = {**instances[1], 'id': 4, 'worker_id': 2}
    if case == 'starting':
        instances[5] = {**instances[1], 'id': 5, 'state': 'starting',
                        'computed_resource_claim': {'vram': {'0': 1}}}
    elif case == 'missing_claim':
        del instances[2]['computed_resource_claim']
    elif case == 'missing_profile':
        del raw['pools']['arbitrary-model-2']['shared_gpu_vram_bytes']
    elif case == 'unmanaged':
        instances[5] = {**instances[1], 'id': 5, 'model_id': 99}
    elif case == 'low_physical_vram':
        workers[1]['status']['gpu_devices'][0]['memory']['total'] = 11
    else:
        instances[2]['computed_resource_claim']['vram']['0'] = 8
    policy = Policy.model_validate(raw)
    candidates, reasons = ready_candidates(policy, catalog_routes(policy, catalog), *native)
    assert candidates['arbitrary-model-1'] == {}
    assert candidates['arbitrary-model-2'] == {}
    assert reasons['arbitrary-model-1'] == reasons['arbitrary-model-2'] == 'gpu_budget_unverified'
    assert set(candidates['arbitrary-model-3']) == {3}


def test_gpu_budget_revision_preserves_legacy_configuration_identity():
    raw, catalog, _ = topology(1)
    policy = Policy.model_validate(raw)
    names = catalog_routes(policy, catalog)
    legacy = policy.model_dump(exclude={'replica_source'})
    del legacy['shared_gpu_vram_budgets']
    for pool in legacy['pools'].values():
        del pool['replica_routes'], pool['shared_gpu_vram_bytes'], pool['guard_backend_version']
    source = 'http://mock/v2/'
    expected = sha256(json.dumps([legacy, names, source], sort_keys=True).encode()).hexdigest()
    assert policy_hash(policy, names, source) == expected
    raw['shared_gpu_vram_budgets'] = {'gpu-type': 16}
    assert policy_hash(Policy.model_validate(raw), names, source) != expected


@pytest.mark.parametrize('peers', [None, [], [{'worker_id': 2, 'gpu_indexes': [0]}]])
def test_delegated_metadata_without_subordinates_is_single_worker(peers):
    raw, catalog, native = topology(1)
    native[2][1]['distributed_servers'] = {'mode': 'delegated', 'download_model_files': True,
                                          'subordinate_workers': peers}
    policy = Policy.model_validate(raw)
    candidates, _ = ready_candidates(policy, catalog_routes(policy, catalog), *native)
    assert sorted(candidates['arbitrary-model-1']) == ([] if peers else [1])


def test_native_read_rejects_incomplete_pages_and_filters_heartbeat_noise():
    _, _, native = topology(2)

    async def read(complete):
        def endpoint(request):
            assert request.method == 'GET'
            kind = request.url.path.rsplit('/', 1)[-1]
            rows = native[RESOURCES.index(kind)]
            total = len(rows) + int(not complete and kind == 'model-routes')
            return httpx.Response(200, json={'items': list(rows.values()),
                                             'pagination': {'totalPage': 1, 'total': total}})

        async with httpx.AsyncClient(base_url='http://mock/v2/', transport=httpx.MockTransport(endpoint)) as client:
            return await read_snapshot(client)

    assert asyncio.run(read(True)) == list(native)
    with pytest.raises(ValueError, match='List changed'):
        asyncio.run(read(False))
    worker = native[0][1]
    assert event_identity('workers', worker) == event_identity('workers', {**worker, 'heartbeat_time': 'new'})
    assert event_identity('workers', worker) != event_identity('workers', {**worker, 'state': 'not_ready'})
    assert event_identity('workers', worker) != event_identity('workers', {**worker, 'system_reserved': {'vram': 1}})
    instance = native[2][1]
    assert event_identity('model-instances', instance) != event_identity(
        'model-instances', {**instance, 'computed_resource_claim': {'vram': {'0': 1}}})


def test_publication_uses_real_postgres_lock_and_expires(env):
    _, service, settings, *_ = env
    raw, catalog, native = topology(4)
    policy = Policy.model_validate(raw)
    observation = Capacity(policy, settle=5, ttl=30)
    evaluate(observation, policy, catalog, native, 1)
    budget = evaluate(observation, policy, catalog, native, 6)
    revision = policy_hash(policy, catalog_routes(policy, catalog), settings.gpustack_url)
    assert status(service.db)['state'] == 'waiting_for_snapshot'

    async def write():
        async with await psycopg.AsyncConnection.connect(settings.database_url, autocommit=True, row_factory=dict_row) as conn:
            observed = (await (await conn.execute('SELECT clock_timestamp() AS at')).fetchone())['at']
            assert await publish(conn, revision, observed, budget) == {'generation': 1}
            assert await publish(conn, revision, observed - timedelta(seconds=1), budget) is None
            assert await publish(conn, revision, observed - timedelta(seconds=TTL+1), budget) is None
            assert await publish(conn, revision, observed + timedelta(microseconds=1), budget) == {'generation': 2}

    asyncio.run(write())
    view = status(service.db)
    assert view['state'] == 'fresh' and view['global']['total'] == 4 and view['generation'] == 2
    with service.db.transaction() as conn:
        conn.execute("UPDATE capacity_snapshot SET observed_at=now()-interval '40 seconds', expires_at=now()-interval '10 seconds'")
    view = status(service.db)
    assert view['state'] == 'expired' and view['global']['total'] == 0
    assert all(pool['total'] == 0 and not pool['ready_ids'] for pool in view['pools'].values())


def test_observer_polls_when_watch_unavailable_and_keeps_generation_on_failed_read(env, monkeypatch, tmp_path):
    _, service, settings, *_ = env
    raw, catalog, native = topology(2)
    settings.catalog_file.write_text(json.dumps(catalog))
    policy_file = tmp_path/'capacity-policy.json'
    policy_file.write_text(json.dumps(raw)); policy_file.chmod(0o600)
    key_file = tmp_path/'capacity-key'
    key_file.write_text('test-read-only-token'); key_file.chmod(0o600)
    monkeypatch.setenv('MAAS_CAPACITY_GPUSTACK_KEY_FILE', str(key_file))
    stop, views = Event(), []
    monkeypatch.setattr(capacity_observe, 'INTERVAL', 0.01)
    monkeypatch.setattr(capacity_observe, 'SETTLE', 0)

    def endpoint(request):
        assert request.method == 'GET'
        if request.url.params.get('watch') == 'true':
            return httpx.Response(503)
        kind = request.url.path.rsplit('/', 1)[-1]
        if len(views) == 2 and kind == 'models':
            return httpx.Response(503)
        rows = native[RESOURCES.index(kind)]
        return httpx.Response(200, json={'items': list(rows.values()),
                                         'pagination': {'totalPage': 1, 'total': len(rows)}})

    def report():
        views.append(status(service.db))
        if len(views) == 3:
            native[0][1]['unreachable'] = True
        if len(views) == 4:
            stop.set()

    factory = httpx.AsyncClient
    monkeypatch.setattr(capacity_observe.httpx, 'AsyncClient',
                        lambda **kwargs: factory(transport=httpx.MockTransport(endpoint), **kwargs))

    async def run():
        await asyncio.wait_for(capacity_observe.observe(settings, policy_file, stop, report), timeout=5)

    asyncio.run(run())
    assert [view['generation'] for view in views] == [1, 2, 2, 3]
    assert [view['global']['total'] for view in views] == [0, 2, 2, 1]
    assert views[1]['expires_at'] == views[2]['expires_at']
