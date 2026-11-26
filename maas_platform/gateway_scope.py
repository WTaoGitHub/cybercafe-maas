"""Publish read-only Higress route and endpoint evidence from its own network namespace."""
import argparse
import json
import os
from pathlib import Path
import re
import time
from urllib.request import ProxyHandler, build_opener

ROUTE = re.compile(r'ai-route-route-(\d+)(\.fallback)?\.internal')
CLUSTER = re.compile(r'outbound\|80\|\|model-(\d+)-(\d+)\.static')


def route_endpoints(configs, clusters):
    hosts = {}
    for cluster in clusters['cluster_statuses']:
        name = cluster['name']
        hosts[name] = [] if name in hosts else cluster.get('host_statuses', [])
    result = {}
    for config in configs['configs']:
        for host in config['route_config']['virtual_hosts']:
            for route in host.get('routes', []):
                match = ROUTE.fullmatch(route.get('name',''))
                if not match:
                    continue
                key = match[1]
                if result.get(key) == {}:
                    continue
                try:
                    if match[2]:
                        # Native cross-model fallback is outside the guarded PP contract.
                        # A local refusal has no upstream destination.
                        if route.get('direct_response', {}).get('status') != 503 or 'route' in route:
                            raise ValueError
                        continue
                    action = route['route']
                    if (action.get('request_mirror_policies') or host.get('request_mirror_policies')
                            or 'internal_redirect_policy' in action or 'internal_redirect_action' in action
                            or route.get('typed_per_filter_config') or action.get('retry_policy_typed_config')
                            or host.get('retry_policy_typed_config')
                            or 'cluster_header' in action or 'cluster_specifier_plugin' in action):
                        raise ValueError
                    if 'cluster' in action:
                        targets = [{'name':action['cluster'],'weight':1}]
                    else:
                        weights = action['weighted_clusters']
                        if set(weights) - {'clusters','total_weight'}:
                            raise ValueError
                        targets = weights['clusters']
                    endpoints = {}
                    for target in targets:
                        if set(target) - {'name','weight'} or type(target['weight']) is not int:
                            raise ValueError
                        if target['weight'] == 0:
                            continue
                        cluster = CLUSTER.fullmatch(target['name'])
                        members = hosts[target['name']]
                        if not cluster or len(members) != 1:
                            raise ValueError
                        address = members[0]['address']['socket_address']
                        if cluster[2] in endpoints:
                            raise ValueError
                        endpoints[cluster[2]] = [int(cluster[1]), address['address'], address['port_value']]
                    # Paths/hosts under the same route must select the same complete set.
                    if key in result and result[key] != endpoints:
                        raise ValueError
                    result[key] = endpoints
                except (KeyError, ValueError, TypeError):
                    result[key] = {}
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--admin-port', type=int, default=15000)
    args = parser.parse_args()
    origin = f'http://127.0.0.1:{args.admin_port}'
    opener = build_opener(ProxyHandler({}))
    def read(path):
        with opener.open(origin+path, timeout=2) as response:
            limit = 16 * 1024 * 1024
            data = response.read(limit + 1)
            if len(data) > limit:
                raise ValueError('Oversized Envoy response')
            return json.loads(data)
    os.umask(0o077)
    while True:
        observed = time.time()
        try:
            configs = read('/config_dump?resource=dynamic_route_configs')
            endpoints = route_endpoints(configs, read('/clusters?format=json'))
            if configs != read('/config_dump?resource=dynamic_route_configs'):
                endpoints = {}
        except (OSError, ValueError, KeyError, TypeError):
            endpoints = {}
        tmp = args.output.with_suffix('.new')
        tmp.write_text(json.dumps({'observed':observed,'routes':endpoints}))
        tmp.replace(args.output)
        time.sleep(2)


if __name__ == '__main__':
    main()
