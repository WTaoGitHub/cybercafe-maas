"""A native shared PP filter excludes the entire GPU including its second replica."""
from types import SimpleNamespace

import pytest

from maas_platform.capacity_observe import gateway_candidates
from maas_platform.gateway_scope import route_endpoints

from scripts.maas.runtime.reconcile_pp_quarantine import check_provider, filter_body, selected
from scripts.maas.runtime.reconcile_gateway_capacity import DESTINATION, MODEL_MATCH


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
