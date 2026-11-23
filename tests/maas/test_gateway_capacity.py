import sys
from threading import Event
from types import SimpleNamespace

import pytest

from scripts.maas.runtime.reconcile_gateway_capacity import (
    DESTINATION, MANAGED, MODEL_MATCH, OWNER, endpoints, filter_body, reconcile,
)


def registry(instance, address):
    return {'name': f'model-1-{instance}', 'type': 'static', 'protocol': 'http', 'domain': address}


def test_current_native_destinations_reconcile_without_fixed_worker_ids():
    annotations = {MODEL_MATCH: 'ocr', DESTINATION: '50% model-1-2.static:80\n50% model-1-3.static:80'}
    native = [registry(1, '10.0.0.1:40000'), registry(2, '10.0.0.2:40001')]
    assert endpoints(annotations, native, 1, 'ocr') == [('10.0.0.2', 40001)]
    native.append(registry(3, '10.0.0.2:40003'))
    assert endpoints(annotations, native, 1, 'ocr') == [('10.0.0.2', 40001), ('10.0.0.2', 40003)]
    assert endpoints({MODEL_MATCH: 'ocr'}, native, 1, 'ocr') == []


@pytest.mark.parametrize('destination,domain', [
    ('100% model-2-1.static:80', '10.0.0.1:40000'),
    ('100% model-1-1.dns:80', '10.0.0.1:40000'),
    ('100% model-1-1.static:80', '127.0.0.1:40000'),
    ('100% model-1-1.static:80', '8.8.8.8:40000'),
])
def test_gateway_reconciler_refuses_unrelated_or_unsupported_backends(destination, domain):
    with pytest.raises(ValueError):
        endpoints({MODEL_MATCH: 'ocr', DESTINATION: destination}, [registry(1, domain)], 1, 'ocr')


def test_envoy_filter_keeps_the_native_route_match_and_rewrite():
    cluster_patch, route_patch = filter_body('higress-system', 3, [('10.0.0.1', 1234)])['spec']['configPatches']
    cluster = cluster_patch['patch']['value']
    assert cluster['lb_policy'] == 'LEAST_REQUEST'
    assert cluster['max_requests_per_connection'] == 1
    assert cluster['common_lb_config']['healthy_panic_threshold']['value'] == 0
    assert cluster['health_checks'][0]['http_health_check']['path'] == '/health'
    assert route_patch['match']['routeConfiguration']['vhost']['route']['name'] == 'ai-route-route-3.internal'
    assert route_patch['patch'] == {'operation': 'MERGE', 'value': {'route': {'cluster': cluster['name']}}}


def test_reconcile_updates_topology_and_removes_owned_filter_without_uid(monkeypatch):
    stop = Event()
    applied = []
    resources = {}

    def ingress(*args, **kwargs):
        instance = len(applied) + 1
        return SimpleNamespace(metadata=SimpleNamespace(labels={MANAGED: 'true'}, annotations={
            MODEL_MATCH: 'ocr', DESTINATION: f'100% model-1-{instance}.static:80',
        }))

    def get(*args, **kwargs):
        if args[-1] == 'default':
            return {'spec': {'registries': [
                registry(1, '10.0.0.1:40000'), registry(2, '10.0.0.2:40001'),
            ]}}
        return resources[args[-1]]

    def write(*args, **kwargs):
        body = args[-1]
        cluster = body['spec']['configPatches'][0]['patch']['value']
        applied.append(cluster['load_assignment']['endpoints'][0]['lb_endpoints'][0])
        body['metadata']['resourceVersion'] = str(len(applied))
        resources[body['metadata']['name']] = body
        if len(applied) == 2:
            stop.set()
        return body

    def delete(*args, **kwargs):
        assert 'body' not in kwargs
        assert resources[args[-1]]['metadata']['labels'][OWNER]
        resources.pop(args[-1])

    api = SimpleNamespace(get_namespaced_custom_object=get, create_namespaced_custom_object=write,
                          replace_namespaced_custom_object=write, delete_namespaced_custom_object=delete)
    client = SimpleNamespace(NetworkingV1Api=lambda: SimpleNamespace(read_namespaced_ingress=ingress),
                             CustomObjectsApi=lambda: api)
    monkeypatch.setitem(sys.modules, 'kubernetes', SimpleNamespace(
        client=client, config=SimpleNamespace(load_kube_config=lambda **kwargs: None),
    ))
    monkeypatch.setitem(sys.modules, 'kubernetes.client.exceptions', SimpleNamespace(ApiException=RuntimeError))

    reconcile(SimpleNamespace(kubeconfig='fixture', namespace='test', route_id=2,
                              model_id=1, model_name='ocr', interval=0), stop)

    assert len(applied) == 2 and applied[0] != applied[1]
    assert not resources
