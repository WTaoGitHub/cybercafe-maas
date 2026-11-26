"""Enforce guarded native routes' eligible GPUs on its built-in Higress gateway.

Run beside the gateway with its Kubernetes client, the capacity observer's intent
directory mounted read-only, and the native route ID, model ID and upstream name.
MaaS separately verifies the applied Envoy routes and every process before admission.
"""
import argparse
import json
from pathlib import Path
import re
import time

from scripts.maas.runtime.reconcile_gateway_capacity import DESTINATION, GROUP, KIND, MANAGED, MODEL_MATCH, VERSION, endpoints

OWNER = 'maas.ai/pp-quarantine'
MAX_AGE = 30


def selected(intent, route_id, model_id, name, annotations, registries, now):
    if not 0 <= now - intent['observed'] < MAX_AGE:
        raise ValueError('Capacity observation expired')
    route = intent['routes'][str(route_id)]
    if (route['model'], route['name']) != (model_id, name):
        raise ValueError('Capacity route identity changed')
    isolated = set(intent['isolated'])
    # Bind the native instance ID to its actual service address as well as its GPU.
    by_name = {r['name']: r for r in registries}
    if len(by_name) != len(registries) or annotations.get(MODEL_MATCH) != name:
        raise ValueError('Ambiguous registry or ingress')
    allowed = []
    seen = set()
    for line in annotations[DESTINATION].splitlines():
        m = re.fullmatch(rf'(0|[1-9][0-9]{{0,2}})% (model-{model_id}-([1-9][0-9]*))\.static:80', line.strip())
        if not m or int(m[1]) > 100 or m[3] in seen:
            raise ValueError('Unexpected native destination')
        seen.add(m[3])
        peer = route['peers'].get(m[3])
        if m[1] == '0' or peer is None or peer[2] in isolated:
            continue
        entry = by_name.get(m[2])
        if entry is None:
            continue
        actual = endpoints({MODEL_MATCH: name, DESTINATION: line}, [entry], model_id, name)
        if actual == [(peer[0], peer[1])]:
            allowed.append({'name': f'outbound|80||{m[2]}.static', 'weight': int(m[1])})
    return sorted(allowed, key=lambda c: c['name'])


def filter_body(namespace, route_id, clusters):
    name = f'maas-pp-quarantine-{route_id}'
    base = f'ai-route-route-{route_id}'
    def patch(route, action):
        return {'applyTo': 'HTTP_ROUTE', 'match': {'context': 'GATEWAY', 'routeConfiguration': {
            'vhost': {'route': {'name': route}}}}, 'patch': {'operation': 'MERGE', 'value': action}}
    closed = {'direct_response': {'status': 503}}
    # This guarded pool has one native model and no cross-model fallback. Keep a
    # newly appearing fallback ingress closed until a supported policy verifies it.
    patches = [patch(base + '.fallback.internal', closed)]
    primary = base + '.internal'
    if clusters:
        # Protobuf MERGE appends repeated fields. Switch the cluster oneof first so
        # no old weighted destination survives the second patch. Retry policy stays native.
        patches.append(patch(primary, {'route': {'cluster': clusters[0]['name']}}))
        if len(clusters) > 1:
            patches.append(patch(primary, {'route': {'weighted_clusters': {
                'clusters': clusters, 'total_weight': sum(c['weight'] for c in clusters)}}}))
    else:
        patches.append(patch(primary, closed))
    return {'apiVersion': f'{GROUP}/{VERSION}', 'kind': 'EnvoyFilter',
            'metadata': {'name': name, 'namespace': namespace, 'labels': {OWNER: str(route_id)}},
            'spec': {'configPatches': patches}}


def check_provider(spec, route_id):
    base = f'ai-route-route-{route_id}'
    rules = [r for r in spec['matchRules'] if base + '.internal' in r.get('ingress', [])]
    if not rules or any(r.get('configDisable') or r['config'] != {'activeProviderId': base} for r in rules):
        raise ValueError('Unknown AI proxy routing')
    provider, = [p for p in spec['defaultConfig']['providers'] if p['id'] == base]
    if any(provider[k].get('enabled') is not False for k in ('retryOnFailure', 'failover')):
        raise ValueError('Unverified AI proxy redispatch')


def advertised_routes(intent):
    if not isinstance(intent, dict) or not isinstance(intent.get('routes'), dict):
        raise ValueError('Invalid guarded route inventory')
    result = {}
    for ident, route in intent['routes'].items():
        if (not isinstance(ident, str) or not re.fullmatch('[1-9][0-9]*', ident)
                or not isinstance(route, dict) or type(route.get('model')) is not int or route['model'] <= 0
                or not isinstance(route.get('name'), str) or not route['name']):
            continue  # Previously managed routes missing a valid identity are closed independently.
        result[int(ident)] = route['model'], route['name']
    return result


def reconcile(args):
    from kubernetes import client, config
    from kubernetes.client.exceptions import ApiException
    from urllib3.exceptions import HTTPError

    config.load_kube_config(config_file=args.kubeconfig)
    networking, custom = client.NetworkingV1Api(), client.CustomObjectsApi()
    scope = (GROUP, VERSION, args.namespace, KIND)
    errors = (OSError, ValueError, TypeError, KeyError, AttributeError, IndexError, ApiException, HTTPError)
    known = set() if args.all_guarded else {args.route_id}
    while True:
        try:
            intent = json.loads(args.intent.read_text())
            requested = advertised_routes(intent)
        except (OSError, ValueError, TypeError, KeyError):
            requested, intent = {}, {}
        if args.all_guarded:
            known.update(requested)
            try:
                owned = custom.list_namespaced_custom_object(*scope, label_selector=OWNER, _request_timeout=3)
                for item in owned['items']:
                    raw = item['metadata']['labels'][OWNER]
                    if re.fullmatch('[1-9][0-9]*', raw) and item['metadata']['name'] == f'maas-pp-quarantine-{raw}':
                        known.add(int(raw))
            except errors:
                print('Unable to enumerate managed gateway filters', flush=True)
        try:
            bridge = custom.get_namespaced_custom_object('networking.higress.io', 'v1', args.namespace,
                                                        'mcpbridges', 'default', _request_timeout=3)
            proxy = custom.get_namespaced_custom_object('extensions.higress.io', 'v1alpha1', args.namespace,
                                                       'wasmplugins', 'gpustack-ai-proxy', _request_timeout=3)
            registries, provider = bridge['spec']['registries'], proxy['spec']
        except errors:
            registries, provider = [], {}
        for route_id in sorted(known):
            try:
                model, name = requested[route_id] if args.all_guarded else (args.model_id, args.model_name)
                ingress = networking.read_namespaced_ingress(f'ai-route-route-{route_id}.internal',
                                                             args.namespace, _request_timeout=3)
                if (ingress.metadata.labels or {}).get(MANAGED) != 'true':
                    raise ValueError('Unexpected ingress owner')
                check_provider(provider, route_id)
                clusters = selected(intent, route_id, model, name, ingress.metadata.annotations, registries, time.time())
            except errors:
                clusters = []
            body = filter_body(args.namespace, route_id, clusters)
            resource = body['metadata']['name']
            try:
                try:
                    old = custom.get_namespaced_custom_object(*scope, resource, _request_timeout=3)
                except ApiException as exc:
                    if exc.status != 404:
                        raise
                    custom.create_namespaced_custom_object(*scope, body, _request_timeout=3)
                else:
                    if old['metadata'].get('labels', {}).get(OWNER) != str(route_id):
                        print(f'Gateway filter ownership conflict for route {route_id}', flush=True)
                        continue
                    if old['spec'] != body['spec']:
                        body['metadata']['resourceVersion'] = old['metadata']['resourceVersion']
                        custom.replace_namespaced_custom_object(*scope, resource, body, _request_timeout=3)
            except (ApiException, HTTPError) as exc:
                print(f'Gateway write unavailable for route {route_id} ({getattr(exc, "status", type(exc).__name__)})', flush=True)
        time.sleep(2)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--all-guarded', action='store_true', help='Reconcile every guarded route in the capacity intent')
    parser.add_argument('--route-id', type=int)
    parser.add_argument('--model-id', type=int)
    parser.add_argument('--model-name')
    parser.add_argument('--intent', type=Path, required=True)
    parser.add_argument('--namespace', default='higress-system')
    parser.add_argument('--kubeconfig', default='/var/lib/gpustack/higress/kubeconfig')
    args = parser.parse_args()
    if args.all_guarded and any(v is not None for v in (args.route_id, args.model_id, args.model_name)):
        parser.error('Use --all-guarded on its own')
    if not args.all_guarded and not (args.model_name and (args.route_id or 0) > 0 and (args.model_id or 0) > 0):
        parser.error('Supply the native route and model identity or --all-guarded')
    reconcile(args)
