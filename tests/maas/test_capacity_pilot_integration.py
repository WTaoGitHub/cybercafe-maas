"""Four-model capacity rehearsal on a disposable PostgreSQL database ending in _test."""
import asyncio
import json

import psycopg
from psycopg.rows import dict_row
import pytest

from maas_platform.capacity import Capacity
from maas_platform.capacity_admission import configuration
from maas_platform.capacity_observe import publish
from maas_platform.errors import ServiceError
from maas_platform.sync_ingress import SyncIngress
from test_capacity import evaluate, topology
from test_integration import env, submit


def test_four_model_observe_enforce_topology_changes_and_rollback(env, tmp_path):
    client, service, settings, auth, _ = env
    settings.max_running = settings.max_project_running = 8
    settings.capacity_policy_file = tmp_path / 'pilot-capacity.json'
    raw, catalog, native = topology(4)
    names = list(catalog)
    a, b, c, d = names
    for name, model in catalog.items():
        model.update(driver='mock', revision='pilot', max_runtime_s=60)
        raw['pools'][name]['slots_per_replica'] = 2
    settings.catalog_file.write_text(json.dumps(catalog))
    bindings = json.loads(settings.bindings_file.read_text())
    bindings['credentials'][0]['models'] = names
    settings.bindings_file.write_text(json.dumps(bindings))
    settings.capacity_policy_file.write_text(json.dumps(raw))
    settings.capacity_policy_file.chmod(0o600)
    policy, _, revision = configuration(settings)
    capacity = Capacity(policy, settle=5, ttl=30)
    identity = auth.authenticate('gpustack_registered_a')
    ingress = SyncIngress(service)

    def observe(at):
        budget = evaluate(capacity, policy, catalog, native, at)

        async def write():
            async with await psycopg.AsyncConnection.connect(settings.database_url, row_factory=dict_row) as conn:
                observed = (await (await conn.execute('SELECT clock_timestamp() at')).fetchone())['at']
                return await publish(conn, revision, observed, budget)

        assert asyncio.run(write())
        return budget['global']['total']

    assert observe(1) == 0
    service.pause(True)
    with pytest.raises(ServiceError) as unavailable:
        service.set_capacity_mode(True)
    assert unavailable.value.code == 'CAPACITY_UNAVAILABLE'
    assert observe(6) == 8
    assert service.set_capacity_mode(True) == 'enforce'
    service.pause(False)

    dispatched = []
    for model in (a, a, b, b, c, c):
        accepted = submit(client, 'test_agent_pilot_' + str(len(dispatched)), model=model, max_attempts=1)
        assert accepted.status_code == 202
        task = service.claim()
        assert task['id'] == accepted.json()['id']
        service.begin_backend(task)
        dispatched.append(task)
    waiting = submit(client, 'test_agent_waiting', model=d, max_attempts=1).json()['id']
    assert service.claim() is None
    sync = [ingress.begin(identity, d, 60) for _ in range(2)]

    workers = native[0]
    workers[2]['unreachable'] = workers[3]['unreachable'] = True
    assert observe(7) == 4
    assert service.claim() is None
    with pytest.raises(ServiceError) as full:
        ingress.begin(identity, b, 30)
    assert full.value.code == 'CAPACITY_UNAVAILABLE'

    for worker in workers.values():
        worker['unreachable'] = True
    assert observe(8) == 0
    for task in dispatched:
        service.complete_backend(task)
        assert service.finish(task, output={'test': 'pilot'})
    for token in sync:
        ingress.finish(token, True)
    assert service.claim() is None

    workers[4]['unreachable'] = False
    assert observe(9) == 0
    assert observe(14) == 2
    resumed = service.claim()
    assert resumed['id'] == waiting
    with service.db.transaction() as conn:
        conn.execute('UPDATE capacity_snapshot SET expires_at=clock_timestamp()')
    with pytest.raises(ServiceError) as stale:
        service.begin_backend(resumed)
    assert stale.value.code == 'DISPATCH_DEFERRED'
    assert service.claim() is None

    service.pause(True)
    assert service.set_capacity_mode(False) == 'static'
    with service.db.transaction() as conn:
        conn.execute('UPDATE tasks SET next_run=clock_timestamp() WHERE id=%s', (waiting,))
    service.pause(False)
    assert service.claim()['id'] == waiting
    assert service.metrics()['successful_tasks'] == 6
