"""Only run against the disposable PostgreSQL supplied by the standard fixture."""
import asyncio
from concurrent.futures import ThreadPoolExecutor
import json

import httpx
import psycopg
from psycopg.rows import dict_row
import pytest

from maas_platform.capacity import limits
from maas_platform.capacity_admission import configuration
from maas_platform.capacity_observe import publish
from maas_platform.errors import ServiceError
from maas_platform.sync_ingress import SyncIngress
from test_integration import env, submit
from test_multi_executor_integration import group


@pytest.fixture
def dynamic(env, tmp_path):
    client, service, settings, auth, _ = env
    settings.max_running = settings.max_project_running = 8
    settings.max_queued = settings.max_project_queued = 25
    settings.capacity_policy_file = tmp_path/'capacity-policy.json'
    model = {'driver': 'mock', 'revision': '1', 'max_runtime_s': 30}
    settings.catalog_file.write_text(json.dumps({
        'mock.echo': {**model, 'upstream_model': 'route-a'},
        'mock.alias': {**model, 'upstream_model': 'route-a-alias'},
        'mock.other': {**model, 'upstream_model': 'route-b'},
    }))
    bindings = json.loads(settings.bindings_file.read_text())
    for row in bindings['credentials']:
        row['models'].extend(['mock.alias', 'mock.other'])
    settings.bindings_file.write_text(json.dumps(bindings))
    common = {'cluster_id': 1, 'owner_principal_id': 1, 'gpu_names': ['example'],
              'slots_per_replica': 1, 'hard_limit': 8, 'gateway_verified': True}
    settings.capacity_policy_file.write_text(json.dumps({'global_limit': 8, 'sync_percent': 25, 'pools': {
        'a': {**common, 'model_id': 1, 'routes': {'mock.echo': 1, 'mock.alias': 3}, 'queue_limit': 12, 'sync_percent': 25},
        'b': {**common, 'model_id': 2, 'routes': {'mock.other': 2}, 'queue_limit': 2},
    }}))
    settings.capacity_policy_file.chmod(0o600)
    _, _, revision = configuration(settings)

    def set_capacity(a, b):
        async def write():
            async with await psycopg.AsyncConnection.connect(settings.database_url, autocommit=True, row_factory=dict_row) as c:
                observed = (await (await c.execute('SELECT clock_timestamp() at')).fetchone())['at']
                return await publish(c, revision, observed, {'global': limits(min(8, a+b), 25),
                                     'pools': {'a': limits(a, 25), 'b': limits(b, 0)}})
        assert asyncio.run(write())

    service.pause(True, 'isolated capacity fixture')
    set_capacity(8, 0)
    assert service.set_capacity_mode(True) == 'enforce'
    service.pause(False)
    return client, service, settings, auth.authenticate('gpustack_registered_a'), set_capacity


def test_sync_uses_all_eight_and_concurrent_async_reserves_only_six(dynamic):
    client, service, settings, identity, _ = dynamic
    ingress = SyncIngress(service)
    tokens = [ingress.begin(identity, 'mock.alias', 30) for _ in range(8)]
    with pytest.raises(ServiceError) as full:
        ingress.begin(identity, 'mock.echo', 30)
    assert full.value.code == 'SYNC_CAPACITY'
    for token in tokens:
        ingress.finish(token, True)
    for i in range(9):
        assert submit(client, f'task-{i}').status_code == 202

    # Spawn independently configured executors to exercise database serialization.
    with group(settings, ['claim'] * 8) as (_, replies):
        claimed = [msg['id'] for _ in range(8) if (msg := replies.get(timeout=15)['result'])]
    assert len(set(claimed)) == 6
    with service.db.transaction() as c:
        tasks = c.execute("SELECT * FROM tasks WHERE state='RUNNING'").fetchall()
    for task in tasks:
        service.begin_backend(task)
    tokens = [ingress.begin(identity, 'mock.alias', 30) for _ in range(2)]
    with pytest.raises(ServiceError) as full:
        ingress.begin(identity, 'mock.echo', 30)
    assert full.value.code == 'SYNC_CAPACITY'
    assert service.claim() is None


def test_downgrade_revalidates_preparing_tasks_without_replaying_intents(dynamic):
    client, service, _, identity, set_capacity = dynamic
    for i in range(6):
        assert submit(client, f'task-{i}', max_attempts=1).status_code == 202
    first, *preparing = [service.claim() for _ in range(6)]
    service.begin_backend(first)
    ingress = SyncIngress(service)
    sync = ingress.begin(identity, 'mock.alias', 30)
    set_capacity(4, 0)

    def begin(task):
        try:
            service.begin_backend(task)
            return task['id']
        except ServiceError as exc:
            assert exc.code == 'DISPATCH_DEFERRED'
            return None

    with ThreadPoolExecutor(max_workers=5) as workers:
        authorized = list(filter(None, workers.map(begin, preparing)))
    assert len(authorized) == 2
    assert not service.defer_dispatch(first)
    with service.db.transaction() as c:
        rows = c.execute("SELECT id,state,max_attempts,deadline,token FROM tasks").fetchall()
        for original in preparing:
            new = next(row for row in rows if row['id'] == original['id'])
            assert new['deadline'] == original['deadline'] and new['max_attempts'] == 1
            if new['id'] not in authorized:
                assert new['state'] == 'QUEUED' and new['token'] is None
        assert c.execute("SELECT count(*) n FROM backend_activity").fetchone()['n'] == 3
        assert c.execute("SELECT count(*) n FROM task_attempts WHERE state='DEFERRED'").fetchone()['n'] == 3
    ingress.finish(sync, True)
    for task in [first, *(task for task in preparing if task['id'] in authorized)]:
        service.complete_backend(task)
        assert service.finish(task, output={'ok': True})
    with service.db.transaction() as c:
        c.execute("UPDATE tasks SET next_run=now() WHERE state='QUEUED'")
    resumed = service.claim()
    assert resumed['id'] in {task['id'] for task in preparing} - set(authorized)
    service.begin_backend(resumed)
    service.complete_backend(resumed)
    assert service.finish(resumed, output={'ok': True})
    with service.db.transaction() as c:
        assert c.execute('SELECT count(*) n FROM task_usage_events WHERE task_id=%s', (resumed['id'],)).fetchone()['n'] == 1


def test_queue_pool_and_requeue_credit_with_healthy_pool_bypass(dynamic):
    client, service, _, _, set_capacity = dynamic
    set_capacity(0, 3)
    a = submit(client, 'pool-a').json()['id']
    b = submit(client, 'pool-b', model='mock.other').json()['id']
    running = service.claim()
    assert running['id'] == b
    assert submit(client, 'pool-b-second', model='mock.other').status_code == 202
    assert submit(client, 'pool-b-full', model='mock.other').json()['error']['code'] == 'QUEUE_FULL'
    # A full pool does not override an accepted task's existing idempotency key.
    assert submit(client, 'pool-b', model='mock.other').json()['id'] == b
    set_capacity(0, 0)
    with pytest.raises(ServiceError) as deferred:
        service.begin_backend(running)
    assert deferred.value.code == 'DISPATCH_DEFERRED'
    assert service.claim() is None
    with service.db.transaction() as c:
        c.execute("UPDATE tasks SET deadline=clock_timestamp()-interval '1 second' WHERE id=%s", (a,))
    assert service.claim() is None
    assert client.get('/maas/v1/tasks/'+a, headers={'Authorization': 'Bearer gpustack_registered_a'}).json()['status'] == 'FAILED'


def test_unknown_quarantines_distinct_routes_in_same_pool_and_late_completion_reopens(dynamic):
    client, service, _, identity, set_capacity = dynamic
    set_capacity(4, 3)
    first = submit(client, 'uncertain').json()['id']
    original = service.claim()
    assert original['id'] == first
    service.begin_backend(original)
    service.finish(original, error=ServiceError('TRANSPORT_TIMEOUT', 'Uncertain execution'))
    same_pool = submit(client, 'alias', model='mock.alias').json()['id']
    other_pool = submit(client, 'elsewhere', model='mock.other').json()['id']
    assert service.claim()['id'] == other_pool
    with pytest.raises(ServiceError) as blocked:
        SyncIngress(service).begin(identity, 'mock.alias', 30)
    assert blocked.value.code == 'BACKEND_QUARANTINED'
    assert service.claim() is None
    service.complete_backend({**original, 'id': other_pool})
    assert service.claim() is None
    service.complete_backend(original)
    assert service.claim()['id'] == same_pool
    with service.db.transaction() as c:
        ledger = c.execute('SELECT state,dispatch_pool_id,resolution_reason FROM backend_activity WHERE token=%s',
                           (original['token'],)).fetchone()
    assert ledger == {'state': 'COMPLETED', 'dispatch_pool_id': 1, 'resolution_reason': 'Transport confirmed completion'}
    assert service.get(identity, first)['state'] == 'FAILED' and service.metrics()['successful_tasks'] == 0


def test_stale_snapshot_and_mixed_config_fail_closed_and_require_drained_switch(dynamic):
    client, service, settings, identity, _ = dynamic
    with pytest.raises(ServiceError) as mode:
        service.set_capacity_mode(False)
    assert mode.value.code == 'MAINTENANCE_REQUIRED'
    with service.db.transaction() as c:
        c.execute("""UPDATE capacity_snapshot SET observed_at=now()-interval '40 seconds',
                     expires_at=now()-interval '10 seconds'""")
    with pytest.raises(ServiceError) as stale:
        SyncIngress(service).begin(identity, 'mock.echo', 30)
    assert stale.value.code == 'CAPACITY_UNAVAILABLE'
    assert submit(client, 'while-stale').status_code == 202
    assert service.claim() is None
    settings.capacity_policy_file = None
    assert submit(client, 'wrong-config').status_code == 503
    service.pause(True)
    assert service.set_capacity_mode(False) == 'static'
    service.pause(False)
    assert service.claim()['model'] == 'mock.echo'


def test_sync_rechecks_capacity_after_input_and_never_sends_when_pool_drops(dynamic):
    _, service, settings, identity, set_capacity = dynamic
    catalog = json.loads(settings.catalog_file.read_text())
    catalog['mock.echo']['driver'] = 'gpustack.ocr'
    settings.catalog_file.write_text(json.dumps(catalog))
    calls = []
    ingress = SyncIngress(service, lambda **kwargs: httpx.AsyncClient(
        transport=httpx.MockTransport(lambda request: calls.append(request) or httpx.Response(200, json={'choices': []})),
        **kwargs))

    async def prepare():
        with service.db.transaction() as c:
            assert c.execute('SELECT count(*) n FROM sync_activity').fetchone()['n'] == 0
        await asyncio.to_thread(set_capacity, 0, 4)
        return {'model': 'mock.echo'}

    with pytest.raises(ServiceError) as removed:
        asyncio.run(ingress.infer(identity, {'model': 'mock.echo'}, prepare_input=prepare))
    assert removed.value.code == 'CAPACITY_UNAVAILABLE' and not calls
    with service.db.transaction() as c:
        assert c.execute('SELECT count(*) n FROM sync_activity').fetchone()['n'] == 0
