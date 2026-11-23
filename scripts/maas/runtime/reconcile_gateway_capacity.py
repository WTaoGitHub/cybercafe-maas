"""Prefer idle replicas of an existing GPUStack route on the built-in Higress."""
import argparse
import ipaddress
import json
import re
import signal
import threading
from uuid import uuid4


GROUP, VERSION, KIND = 'networking.istio.io', 'v1alpha3', 'envoyfilters'
DESTINATION = 'higress.io/destination'
MODEL_MATCH = 'higress.io/exact-match-header-x-higress-llm-model'
MANAGED = 'gpustack.ai/managed'
OWNER = 'maas.ai/gateway-capacity'


def endpoints(annotations, registries, model_id, model_name):
    if annotations.get(MODEL_MATCH) != model_name:
        raise ValueError('The native ingress does not match the configured model name')
    pattern = re.compile(rf'[1-9][0-9]*% (model-{model_id}-[1-9][0-9]*)\.static:80')
    registries = {registry['name']: registry for registry in registries}
    addresses = set()
    for line in annotations.get(DESTINATION, '').splitlines():
        match = pattern.fullmatch(line.strip())
        if not match:
            raise ValueError('Only native direct model instance destinations are supported')
        registry = registries.get(match[1])
        if registry is None:
            # Ingress and registry changes can arrive in either order.
            continue
        if registry.get('type') != 'static' or registry.get('protocol') != 'http' or registry.get('proxyName'):
            raise ValueError('Only direct HTTP registries are supported')
        address, port = registry['domain'].rsplit(':', 1)
        ip = ipaddress.IPv4Address(address)
        if not ip.is_private or ip.is_loopback or ip.is_link_local or not 1 <= int(port) <= 65535:
            raise ValueError('A private instance endpoint and valid port are required')
        addresses.add((str(ip), int(port)))
    return sorted(addresses)


def filter_body(namespace, route_id, addresses):
    name = f'maas-least-inflight-{route_id}'
    route = f'ai-route-route-{route_id}.internal'
    cluster = {
        'name': name,
        'type': 'STATIC',
        'connect_timeout': '2s',
        # The direct OCR servers can close idle HTTP/1 connections before Envoy reuses them.
        'max_requests_per_connection': 1,
        'lb_policy': 'LEAST_REQUEST',
        'least_request_lb_config': {'choice_count': 32},
        'common_lb_config': {'healthy_panic_threshold': {'value': 0}},
        'health_checks': [{
            'timeout': '1s', 'interval': '2s', 'unhealthy_threshold': 1, 'healthy_threshold': 1,
            'http_health_check': {'path': '/health'},
        }],
        'load_assignment': {'cluster_name': name, 'endpoints': [{'lb_endpoints': [
            {'endpoint': {'address': {'socket_address': {'address': ip, 'port_value': port}}}}
            for ip, port in addresses
        ]}]},
    }
    return {
        'apiVersion': f'{GROUP}/{VERSION}', 'kind': 'EnvoyFilter',
        'metadata': {'name': name, 'namespace': namespace, 'labels': {OWNER: 'true'}},
        'spec': {'configPatches': [
            {'applyTo': 'CLUSTER', 'match': {'context': 'GATEWAY'},
             'patch': {'operation': 'ADD', 'value': cluster}},
            {'applyTo': 'HTTP_ROUTE', 'match': {'context': 'GATEWAY', 'routeConfiguration': {
                'vhost': {'route': {'name': route}},
            }}, 'patch': {'operation': 'MERGE', 'value': {'route': {'cluster': name}}}},
        ]},
    }


def reconcile(args, stop):
    from kubernetes import client, config
    from kubernetes.client.exceptions import ApiException

    config.load_kube_config(config_file=args.kubeconfig)
    networking, custom = client.NetworkingV1Api(), client.CustomObjectsApi()
    scope = (GROUP, VERSION, args.namespace, KIND)
    name = f'maas-least-inflight-{args.route_id}'
    owner = uuid4().hex
    resource = None
    previous = None
    try:
        while not stop.is_set():
            try:
                ingress = networking.read_namespaced_ingress(
                    f'ai-route-route-{args.route_id}.internal', args.namespace, _request_timeout=5,
                )
            except ApiException as error:
                if error.status != 404:
                    raise
                addresses = []
            else:
                if (ingress.metadata.labels or {}).get(MANAGED) != 'true':
                    raise ValueError('The selected ingress is not managed by GPUStack')
                bridge = custom.get_namespaced_custom_object(
                    'networking.higress.io', 'v1', args.namespace, 'mcpbridges', 'default', _request_timeout=5,
                )
                addresses = endpoints(
                    ingress.metadata.annotations or {}, bridge['spec'].get('registries') or [],
                    args.model_id, args.model_name,
                )
            if addresses != previous:
                body = filter_body(args.namespace, args.route_id, addresses)
                body['metadata']['labels'][OWNER] = owner
                if resource is None:
                    resource = custom.create_namespaced_custom_object(*scope, body, _request_timeout=5)
                else:
                    body['metadata']['resourceVersion'] = resource['metadata']['resourceVersion']
                    resource = custom.replace_namespaced_custom_object(*scope, name, body, _request_timeout=5)
                previous = addresses
                print(json.dumps({'route_id': args.route_id, 'candidate_count': len(addresses)}), flush=True)
            stop.wait(args.interval)
    finally:
        try:
            current = custom.get_namespaced_custom_object(*scope, name, _request_timeout=5)
        except ApiException as error:
            if error.status != 404:
                raise
        else:
            metadata = current['metadata']
            if metadata.get('labels', {}).get(OWNER) == owner:
                uid = metadata.get('uid')
                precondition = {'body': {'preconditions': {'uid': uid}}} if uid else {}
                custom.delete_namespaced_custom_object(*scope, name, _request_timeout=5, **precondition)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--route-id', required=True, type=int)
    parser.add_argument('--model-id', required=True, type=int)
    parser.add_argument('--model-name', required=True)
    parser.add_argument('--namespace', default='higress-system')
    parser.add_argument('--kubeconfig', default='/var/lib/gpustack/higress/kubeconfig')
    parser.add_argument('--interval', type=float, default=2)
    args = parser.parse_args()
    if min(args.route_id, args.model_id) < 1 or not 1 <= args.interval <= 30:
        parser.error('Use positive IDs and an interval of 1 to 30 seconds')
    stop = threading.Event()
    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, lambda *_: stop.set())
    reconcile(args, stop)


if __name__ == '__main__':
    main()
