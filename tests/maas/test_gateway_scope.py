"""Envoy evidence covers all positive-weight destinations, including retry candidates."""
from copy import deepcopy
import pytest

from maas_platform.gateway_scope import route_endpoints


def fixture():
    name = 'outbound|80||model-17-123.static'
    route = {'name':'ai-route-route-4.internal','route':{'weighted_clusters':{'clusters':[
        {'name':name,'weight':100}]}}}
    config = {'configs':[{'route_config':{'virtual_hosts':[{'routes':[route]}]}}]}
    clusters = {'cluster_statuses':[{'name':name,'host_statuses':[{
        'address':{'socket_address':{'address':'192.0.2.1','port_value':40001}}}]}]}
    return config, route, clusters


@pytest.mark.parametrize('change',['positive','mirror','absent','unknown','multiple','different_paths'])
def test_exact_gateway_scope_or_closed(change):
    config, route, clusters = fixture()
    if change=='positive':
        route['route']['weighted_clusters']['clusters'].append({'name':'disabled-service','weight':0})
    if change=='mirror':
        route['route']['request_mirror_policies']=[{}]
    if change=='absent':
        clusters['cluster_statuses'].clear()
    if change=='unknown':
        route['route']['weighted_clusters']['clusters'][0]['name']='remote-provider'
    if change=='multiple':
        hosts=clusters['cluster_statuses'][0]['host_statuses']
        hosts.append(deepcopy(hosts[0]))
    if change=='different_paths':
        variant = deepcopy(route)
        variant['route']['weighted_clusters']['clusters'][0]['name']='different-service'
        config['configs'][0]['route_config']['virtual_hosts'][0]['routes'].append(variant)
    assert route_endpoints(config,clusters) == {'4':{'123':[17,'192.0.2.1',40001]} if change=='positive' else {}}
