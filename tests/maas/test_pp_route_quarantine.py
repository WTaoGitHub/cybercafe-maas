"""A native shared PP filter excludes the entire GPU including its second replica."""
from types import SimpleNamespace
import asyncio
from datetime import datetime, timezone
import json

import psycopg
from psycopg.rows import dict_row
import pytest

from maas_platform.capacity_observe import gateway_candidates, write_gateway_intent
from maas_platform.gateway_scope import route_endpoints

from scripts.maas.runtime.reconcile_pp_quarantine import check_provider, filter_body, selected
from scripts.maas.runtime.reconcile_gateway_capacity import DESTINATION, MODEL_MATCH
from scripts.maas.runtime import pp_grant_window
from test_integration import env


def test_cached_withdrawal_keeps_age_and_can_only_remove_more_cards(env, tmp_path):
    _, _, settings, *_ = env
    path = tmp_path / 'intent.json'
    seen = datetime.now(timezone.utc)
    async def run():
        async with await psycopg.AsyncConnection.connect(settings.database_url, row_factory=dict_row) as conn:
            await conn.execute("""INSERT INTO sync_activity(token,project,model,state,deadline,isolated_gpu_uuid)
                VALUES ('example','test_agent_pp_demo','ocr','UNKNOWN',now(),'gpu-b')""")
            await write_gateway_intent(conn, path, seen, {}, ['gpu-a'])
            assert json.loads(path.read_text()) == dict(observed=seen.timestamp(), routes={}, isolated=['gpu-a', 'gpu-b'])
            await write_gateway_intent(conn, path, seen, {})
            assert json.loads(path.read_text())['isolated'] == ['gpu-b']
    asyncio.run(run())


def test_grant_window_excludes_previously_dispatched_work_and_detects_late_isolated_grants(env):
    _, service, *_ = env
    project = 'test_agent_pp_example'
    with service.db.transaction() as c:
        pp_grant_window.install(c, project)
        for token in ('origin', 'already-dispatched', 'healthy', 'unsafe'):
            c.execute("""INSERT INTO sync_activity(token,project,model,state,deadline)
                VALUES (%s,%s,'ocr','DISPATCHING',clock_timestamp())""", (token, project))
            if token == 'origin':
                c.execute("UPDATE sync_activity SET gpu_uuid='bad',state='UNKNOWN' WHERE token=%s", (token,))
            else:
                if token != 'already-dispatched':
                    c.execute('UPDATE sync_activity SET created_at=clock_timestamp() WHERE token=%s', (token,))
                c.execute('UPDATE sync_activity SET gpu_uuid=%s WHERE token=%s',
                          ('bad' if token == 'unsafe' else 'good', token))
        result = pp_grant_window.read_window(c, project, 'sync', 'origin')['traffic']
        assert result['sync']['healthy_grants'] == 1
        assert result['sync']['seconds_to_healthy_grant'] > 0
        assert result['sync']['grants_to_inflight_dispatches'] == 1
        assert 0 <= result['sync']['seconds_to_any_healthy_grant'] < result['sync']['seconds_to_healthy_grant']
        assert result['sync']['isolated_grants'] == 2
        assert result['async']['seconds_to_healthy_grant'] is None
        c.execute("UPDATE sync_activity SET project='dedicated-pin' WHERE token='origin'")
        with pytest.raises(ValueError, match='No matching project fault'):
            pp_grant_window.read_window(c, project, 'sync', 'origin')
        assert pp_grant_window.read_window(c, project, 'sync', 'origin', 'dedicated-pin')['traffic'] == result
        pp_grant_window.remove(c, project)


def test_native_targets_bind_id_address_gpu_and_quarantine_all_siblings():
    instances = {i: dict(worker_ip=f'192.0.2.{1 if i in (1, 2) else i}', port=5000+i, backend_version='guard')
                 for i in (1, 2, 3, 4)}
    workers = {i: {'ip': v['worker_ip']} for i, v in instances.items()}
    pool = SimpleNamespace(model_id=9, routes={'shared': 7}, guard_backend_version='guard')
    candidates = {'shared': {i: (i, 'offline' if i < 3 else 'online') for i in instances}}
    native = workers, {9: {'backend_version': 'guard'}}, instances, {7: {}}, {}
    routes = gateway_candidates(SimpleNamespace(pools={'shared': pool}), {7: 'pp'}, native, candidates)
    intent = {'observed': 100, 'routes': routes, 'isolated': ['offline']}
    annotations = {MODEL_MATCH: 'pp', DESTINATION: '\n'.join(f'25% model-9-{i}.static:80' for i in instances)}
    registries = [dict(name=f'model-9-{i}', type='static', protocol='http',
                       domain=f"{v['worker_ip']}:{v['port']}") for i,v in instances.items()]
    registries[-1]['domain'] = '192.0.2.4:5555'
    got = selected(intent, 7, 9, 'pp', annotations, registries, 101)
    assert got == [{'name': 'outbound|80||model-9-3.static', 'weight': 25}]
    annotations[DESTINATION] = annotations[DESTINATION].replace('25% model-9-4', '0% model-9-4')
    assert selected(intent, 7, 9, 'pp', annotations, registries, 101) == got
    intent['isolated'].append('online')
    assert not selected(intent, 7, 9, 'pp', annotations, registries, 101)
    with pytest.raises(ValueError):
        selected(intent, 7, 9, 'pp', annotations, registries, 130)
    assert filter_body('ns', 7, [])['spec']['configPatches'][-1]['patch']['value'] == {'direct_response': {'status': 503}}


def test_active_fallback_and_plugin_redispatch_invalidate_evidence():
    spec = {'matchRules': [{'ingress': ['ai-route-route-7.internal'], 'config': {'activeProviderId': 'ai-route-route-7'}}],
            'defaultConfig': {'providers': [{'id':'ai-route-route-7','retryOnFailure':{'enabled':False},
                                             'failover':{'enabled':False}}]}}
    check_provider(spec, 7)
    spec['defaultConfig']['providers'][0]['failover']['enabled'] = True
    with pytest.raises(ValueError):
        check_provider(spec, 7)
    key = 'outbound|80||model-9-3.static'
    routes = [{'name':'ai-route-route-7.internal','route':{'cluster':key}},
              {'name':'ai-route-route-7.fallback.internal','direct_response':{'status':503}}]
    config = {'configs':[{'route_config':{'virtual_hosts':[{'routes':routes}]}}]}
    clusters = {'cluster_statuses':[{'name':key,'host_statuses':[{'address':{'socket_address':
                {'address':'192.0.2.3','port_value':5003}}}]}]}
    assert route_endpoints(config, clusters) == {'7': {'3': [9, '192.0.2.3', 5003]}}
    routes[1] = {'name':'ai-route-route-7.fallback.internal','route':{'cluster':key}}
    assert route_endpoints(config, clusters) == {'7': {}}
