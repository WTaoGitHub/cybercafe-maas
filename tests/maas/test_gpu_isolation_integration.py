"""Admission, worker ownership and routing on the same disposable PostgreSQL."""

import asyncio
import base64
import json

import httpx
from psycopg.types.json import Jsonb
import pytest

from maas_platform.capacity import Capacity
from maas_platform.capacity_admission import configuration
from maas_platform.errors import ServiceError
from maas_platform.pp_ocr_execution import ATTEMPT_HEADER, PATH, TABLE
from maas_platform.quarantine import blocks, unresolved
from maas_platform.worker import Worker
from test_capacity import evaluate, topology
from test_consumer_ocr import png
from test_integration import headers
from test_pp_ocr_execution_integration import env, guarded


@pytest.fixture
def pinned(guarded, tmp_path):
    client, service, replicas = guarded
    settings = service.settings
    settings.max_running = settings.max_project_running = 2
    catalog = settings.catalog()
    name = 'ocr.default'
    catalog[name]['upstream_model'] = 'test_agent_ocr_a'
    settings.catalog_file.write_text(json.dumps(catalog))

    raw, _, native = topology(2)
    workers, models, instances, routes, _ = native
    a, _ = raw['pools'].values()
    a.update(hard_limit=2, routes={name: 1}, replica_routes={}, shared_gpu_vram_bytes=5)
    raw.update(global_limit=2, sync_percent=0, pools={'ocr': a}, shared_gpu_vram_budgets={'gpu-type': 12})
    for index, replica in enumerate(replicas, 1):
        route = ('test_agent_ocr_a', 'test_agent_ocr_b')[index - 1]
        gpu = replica.identity['gpu_uuid']
        replica.identity['route'] = route
        routes[index]['name'] = route
        workers[index].update(name=gpu, system_reserved={'vram': 2})
        workers[index]['status']['gpu_devices'][0].update(uuid=gpu, type='cuda', memory={'total': 16})
        instances[index]['computed_resource_claim'] = {'vram': {'0': 5}}
        models[index].update(replicas=1, gpu_selector={'gpu_ids': [f'{gpu}:cuda:0'], 'gpus_per_replica': 1})
        a['replica_routes'][route] = {'route_id': index, 'model_id': index, 'gpu_uuid': gpu}
    settings.capacity_policy_file = tmp_path / 'test_agent_pinned_capacity.json'
    settings.capacity_policy_file.write_text(json.dumps(raw))
    settings.capacity_policy_file.chmod(0o600)
    policy, _, revision = configuration(settings)
    capacity = Capacity(policy, settle=5, ttl=30)
    evaluate(capacity, policy, catalog, native, 1)
    budget = evaluate(capacity, policy, catalog, native, 6)
    assert budget['pools']['ocr']['replica_routes'] == {
        replica.identity['route']: replica.identity['gpu_uuid'] for replica in replicas}
    with service.db.transaction() as conn:
        conn.execute("""INSERT INTO capacity_snapshot(id,policy_hash,observed_at,expires_at,budget)
                     VALUES(1,%s,now(),now()+interval '30 seconds',%s)""", (revision, Jsonb(budget)))
    service.pause(True, 'test_agent_enable_gpu_isolation')
    service.set_capacity_mode(True)
    service.pause(False)
    return client, service, replicas, capacity, policy, catalog, native


@pytest.mark.parametrize('lost_kind', ['task', 'sync'])
@pytest.mark.parametrize('claimed', [True, False])
def test_only_authenticated_gpu_scope_can_leave_a_peer_available(pinned, monkeypatch, lost_kind, claimed):
    client, service, (first, other), *_ = pinned
    arrivals = []
    original_http = httpx.AsyncClient
    engine_result = {'text': 'test_agent_result', 'lines': [], 'elapsed_ms': 1, 'node': 'test_agent_worker'}

    def gateway(request):
        attempt = request.headers[ATTEMPT_HEADER]
        route = json.loads(request.content)['model']
        arrivals.append((attempt, route))
        if len(arrivals) == 1:
            assert route == first.identity['route']
            # Even with its own key, the wrong GPU cannot claim the chosen route.
            event = {**other.identity, 'route': route, 'attempt': attempt, 'finished': False}
            assert client.post(PATH, json=event, headers=other.headers).status_code == 409
            if claimed:
                first.report(attempt, finished=False)
            raise httpx.ReadError('test_agent_connection_lost', request=request)
        replica = other if route == other.identity['route'] else first
        replica.report(attempt, finished=False)
        replica.report_finished(attempt)
        return httpx.Response(200, json={'choices': [{'message': {'content': json.dumps(engine_result)}}]})

    async def transport(request):
        return await asyncio.to_thread(gateway, request)

    def HTTP(**kwargs):
        return original_http(transport=httpx.MockTransport(transport), **kwargs)

    monkeypatch.setattr('maas_platform.ocr_connector.httpx.AsyncClient', HTTP)
    client.app.state.sync_ingress.client_factory = HTTP
    image = base64.b64encode(png()).decode()

    def invoke(kind, label):
        if kind == 'task':
            response = client.post('/maas/v1/tasks', headers=headers(idem=label),
                json={'model': 'ocr.default', 'input': {'image': {'image_base64': image}}})
            assert response.status_code == 202
            Worker(service).run_once()
            return client.get(response.headers['location'], headers=headers()).json()
        response = client.post('/gpustack/v1/chat/completions', headers=headers(),
            json={'model': 'ocr.default', 'messages': [{'role': 'user', 'content': [{'type': 'image_url',
                'image_url': {'url': 'data:image/png;base64,' + image}}]}]})
        return response.json()

    invoke(lost_kind, 'test_agent_lost')
    attempt, _ = arrivals[0]
    assert attempt.startswith(lost_kind + '.')
    with service.db.transaction() as conn:
        row = conn.execute(f"SELECT state,dispatch_gpu_uuid,gpu_uuid FROM {TABLE[lost_kind]} WHERE token=%s",
                           (attempt.split('.')[1],)).fetchone()
    assert row == {'state': 'UNKNOWN', 'dispatch_gpu_uuid': first.identity['gpu_uuid'],
                   'gpu_uuid': first.identity['gpu_uuid'] if claimed else None}
    healthy_kind = 'task' if lost_kind == 'sync' else 'sync'
    response = invoke(healthy_kind, 'test_agent_healthy')
    if healthy_kind == 'sync':
        assert ('choices' in response) == claimed
    else:
        assert response['status'] == ('SUCCEEDED' if claimed else 'QUEUED')
    assert [route for _, route in arrivals] == (
        [first.identity['route'], other.identity['route']] if claimed else [first.identity['route']])
    if claimed:
        first.report_finished(attempt)
        assert service.metrics()['unknown_backend_activity'] == service.metrics()['unknown_sync_activity'] == 0
        invoke('sync', 'test_agent_recovered')
        assert arrivals[-1][1] == first.identity['route']


def test_changed_gateway_target_revokes_only_its_pinned_route(pinned):
    _, _, (first, _), capacity, policy, catalog, native = pinned
    native[4][2]['fallback_status_codes'] = [503]
    budget = evaluate(capacity, policy, catalog, native, 7)
    assert budget['global']['total'] == 1
    assert budget['pools']['ocr']['replica_routes'] == {first.identity['route']: first.identity['gpu_uuid']}


def test_over_budget_gpu_revokes_only_its_pinned_route(pinned):
    _, _, (first, _), capacity, policy, catalog, native = pinned
    native[2][2]['computed_resource_claim']['vram']['0'] = 13
    budget = evaluate(capacity, policy, catalog, native, 7)
    assert budget['global']['total'] == 1
    assert budget['pools']['ocr']['replica_routes'] == {first.identity['route']: first.identity['gpu_uuid']}


def test_unknown_gpu_blocks_shared_card_and_still_consumes_shared_quota(pinned):
    client, service, (first, other), *_ = pinned
    ingress = client.app.state.sync_ingress
    who = service.auth.authenticate('gpustack_registered_a')
    token, route = ingress.begin_dispatch(who, 'ocr.default', 30)
    assert route == first.identity['route']
    first.report('sync.' + token, finished=False)
    ingress.finish(token, False)
    with service.db.transaction() as conn:
        unknown = unresolved(conn)
    assert blocks(unknown, 'another-model', 'another-route', 99, first.identity['gpu_uuid'])
    assert not blocks(unknown, 'another-model', 'another-route', 99, other.identity['gpu_uuid'])
    assert blocks(unknown, 'another-model', 'unmapped-route', 99)
    service.settings.max_project_running = 1
    with pytest.raises(ServiceError) as blocked:
        ingress.begin_dispatch(who, 'ocr.default', 30)
    assert blocked.value.code == 'SYNC_CAPACITY'
    first.report_finished('sync.' + token)
    assert ingress.begin_dispatch(who, 'ocr.default', 30)[1] == first.identity['route']
