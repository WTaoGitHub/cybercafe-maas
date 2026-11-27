"""GPUStack model counts through the observer, PostgreSQL and both inference ingress paths.

Point MAAS_TEST_DATABASE_URL at a disposable database ending in _test (the fixture
rebuilds its schemas), then run python -m pytest tests/maas/test_gpustack_ready_dispatch.py.
"""
import asyncio
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
import json
from threading import Condition, Event

import httpx
import psycopg
import pytest
from psycopg.rows import dict_row

from maas_platform.capacity import Policy, ready_replica_budget
from maas_platform.capacity_admission import Admission, configuration
from maas_platform.capacity_observe import sample, status
from maas_platform.errors import ServiceError
from maas_platform.maintenance import Maintenance
from maas_platform.sync_ingress import SyncIngress
from maas_platform.worker import Worker
from test_integration import env, headers, submit


def test_ready_count_uses_bound_deployment_and_counts_aliases_once():
    pool = dict(cluster_id=1, owner_principal_id=7, model_id=9, routes={'A': 8, 'alias': 8},
                queue_limit=25, hard_limit=1, slots_per_replica=3)
    policy = Policy(sync_percent=25, pools={'shared': pool})
    model = dict(cluster_id=1, owner_principal_id=7, replicas=99, ready_replicas=4)
    assert ready_replica_budget(policy, {9: model})['global']['total'] == 4
    for invalid in ({'owner_principal_id': 8}, {'cluster_id': 2}, {'ready_replicas': None},
                    {'ready_replicas': -1}, {'ready_replicas': True}):
        assert ready_replica_budget(policy, {9: {**model, **invalid}})['global']['total'] == 0
    assert ready_replica_budget(policy, {})['global']['total'] == 0


def test_historical_unknown_seven_upstream_calls_then_zero(env, tmp_path, monkeypatch):
    client, service, settings, _, _ = env
    project = 'test_agent_ready'
    counts = {'A': 4, 'B': 2, 'C': 1}
    pools, catalog, models = {}, {}, {}
    for ident, (name, count) in enumerate(counts.items(), 1):
        pools[name] = dict(cluster_id=1, owner_principal_id=7, model_id=ident,
                           routes={name: ident}, queue_limit=25)
        catalog[name] = dict(driver='gpustack.ocr', revision='test_agent_ready',
                             upstream_model='route-'+name, max_runtime_s=30)
        models[ident] = dict(id=ident, cluster_id=1, owner_principal_id=7, replicas=99, ready_replicas=count)
    settings.catalog_file.write_text(json.dumps(catalog))
    settings.capacity_policy_file = tmp_path / 'test_agent_policy.json'
    settings.capacity_policy_file.write_text(json.dumps(dict(sync_percent=25, pools=pools)))
    settings.capacity_policy_file.chmod(0o600)
    binding = json.loads(settings.bindings_file.read_text())
    binding['credentials'][0].update(project=project, models=list(counts))
    settings.bindings_file.write_text(json.dumps(binding))
    policy, _, revision = configuration(settings)
    actual_client = httpx.AsyncClient

    def listing(request):
        assert request.url.path == '/v2/models'
        return httpx.Response(200, json={'items': list(models.values()), 'pagination': {'totalPage': 1, 'total': 3}})

    async def observe():
        stop = Event()
        async with actual_client(base_url='http://gpustack.test/v2/', transport=httpx.MockTransport(listing)) as api:
            async with await psycopg.AsyncConnection.connect(settings.database_url, autocommit=True, row_factory=dict_row) as db:
                await sample(api, db, settings.catalog_file, settings.gpustack_url.rstrip('/')+'/v2/',
                             policy, asyncio.Event(), stop, stop.set, None, asyncio.Event(), None)
        return status(service.db)

    with service.db.transaction() as db:
        # Historic attempt from before routing metadata existed, in the same project and model.
        db.execute("""INSERT INTO sync_activity(token,project,model,state,deadline) VALUES
                    ('test_agent_old',%s,'A','UNKNOWN',now()-interval '1 day')""", (project,))
    positive = asyncio.run(observe())
    assert positive['global'] == {'total': 7, 'sync_reserved': 2, 'async_limit': 5}
    assert {k: p['total'] for k, p in positive['pools'].items()} == counts
    assert positive['unknown_capacity']['record_only'] == positive['unknown_capacity']['reconciliation_only'] == 1
    service.pause(True)
    assert service.set_capacity_mode(True) == 'enforce'
    probes = []
    assert not Maintenance(service).resume(lambda: probes.append('ready'))['paused']
    assert probes == ['ready']
    sent, changed, release = Counter(), Condition(), Event()

    async def upstream(request):
        assert request.url.path == "/v1/chat/completions"
        assert "X-MaaS-Execution-Attempt" not in request.headers
        with changed:
            sent[json.loads(request.content)['model']] += 1
            changed.notify_all()
        assert await asyncio.to_thread(release.wait, 20)
        output = {'lines': [], 'text': 'ok', 'elapsed_ms': 1, 'node': 'test_agent_backend'}
        return httpx.Response(200, json={'choices': [{'message': {'content': json.dumps(output)}}]})

    def transport(**kw):
        return actual_client(transport=httpx.MockTransport(upstream), **kw)

    monkeypatch.setattr('maas_platform.ocr_connector.httpx.AsyncClient', transport)
    monkeypatch.setattr('maas_platform.sync_ingress.local_slots', lambda *_: (16, 'available_memory'))
    client.app.state.sync_ingress = SyncIngress(service, transport)
    def sync(name):
        return client.post('/gpustack/v1/chat/completions', headers=headers(), json={'model': name, 'messages': []})

    image = client.post('/maas/v1/artifacts', headers={**headers(), 'Content-Type': 'image/png'},
                        content=b'\x89PNG\r\n\x1a\nfixture').json()['id']
    def enqueue(name, key):
        r = submit(client, 'test_agent_'+key, model=name, max_attempts=1, input={'image': {'artifact_id': image}})
        assert r.status_code == 202
        return r.json()['id']

    tasks = [enqueue(name, str(i)) for i, name in enumerate('AAABC')]
    with ThreadPoolExecutor(max_workers=7) as threads:
        jobs = [threads.submit(Worker(service).run_once) for _ in tasks]
        try:
            with changed:
                assert changed.wait_for(lambda: sum(sent.values()) == 5, timeout=12)
            queued = enqueue('B', 'queued')
            assert service.claim() is None  # Two slots reserved for synchronous calls.
            requests = [threads.submit(sync, 'A')]
            with changed:
                assert changed.wait_for(lambda: sum(sent.values()) == 6, timeout=10)
            assert sync('A').status_code == 429  # A is full even though the global budget has room.
            requests.append(threads.submit(sync, 'B'))
            with changed:
                assert changed.wait_for(lambda: sum(sent.values()) == 7, timeout=10)
            assert sent == {'route-A': 4, 'route-B': 2, 'route-C': 1}
            rejection = sync('A')
            assert rejection.status_code == 429 and rejection.json()['error']['code'] == 'SYNC_CAPACITY'
            with service.db.transaction() as db:
                admission = Admission(db, settings, revision, include_preparing=True)
                assert admission.project_limit == admission.project[project] == admission.total == 7
            for model in models.values():
                model['ready_replicas'] = 0
            zero = asyncio.run(observe())
            assert zero['global'] == {'total': 0, 'sync_reserved': 0, 'async_limit': 0}
            assert all(p['total'] == 0 for p in zero['pools'].values())
            assert sync('B').status_code == 503 and service.claim() is None
        finally:
            release.set()
        assert all(job.result() for job in jobs)
        assert all(request.result().status_code == 200 for request in requests)
    with service.db.transaction() as db:
        admission = Admission(db, settings, revision, include_preparing=True)
        assert admission.project_limit == admission.total == 0
        assert db.execute("SELECT state FROM sync_activity WHERE token='test_agent_old'").fetchone()['state'] == 'UNKNOWN'
    assert sync('C').status_code == 503 and service.claim() is None
    assert client.get('/maas/v1/tasks/'+queued, headers=headers()).json()['attempts'] == 0
    assert sum(sent.values()) == 7
    service.pause(True)
    assert not Maintenance(service).resume(lambda: probes.append('zero'))['paused']
    assert probes == ['ready']
    for model, count in zip(models.values(), counts.values()):
        model['ready_replicas'] = count
    assert asyncio.run(observe())['global']['total'] == 7
    claimed = service.claim()
    assert claimed['id'] == queued
    for model in models.values():
        model['ready_replicas'] = 0
    asyncio.run(observe())
    with pytest.raises(ServiceError, match='deferred'):
        service.begin_backend(claimed)
    row = client.get('/maas/v1/tasks/'+queued, headers=headers()).json()
    assert row['status'] == 'QUEUED' and row['attempts'] == 1
    with service.db.transaction() as db:
        assert db.execute('SELECT state FROM task_attempts WHERE token=%s', (claimed['token'],)).fetchone()['state'] == 'DEFERRED'
        assert not db.execute('SELECT 1 FROM backend_activity WHERE token=%s', (claimed['token'],)).fetchone()
    assert sum(sent.values()) == 7
