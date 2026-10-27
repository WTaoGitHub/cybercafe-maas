from pathlib import Path
import pytest
from maas_platform.container_ingress import container_http_clients, render_nginx
from maas_platform.boundary_policy import rules

BASE = {'node':'XZ-31-001','interface':'enp3s0','bridge_subnets':['172.17.0.0/16']}
CLIENT = {'interface':'br-bac8bba30f24','source_cidr':'172.18.0.0/16',
          'listen_address':'172.18.0.1','port':18831}


def test_exception_is_scoped_to_bridge_source_destination_and_business_port():
    cfg={**BASE,'container_http_clients':[CLIENT]}
    extra=set(rules(cfg,4))-set(rules(BASE,4))
    assert extra=={'-A MAAS-P4-EARLY -i br-bac8bba30f24 -s 172.18.0.0/16 -d 172.18.0.1/32 -p tcp --dport 18831 -j RETURN'}
    assert rules(cfg,6)==rules(BASE,6)
    rows=rules(cfg,4)
    assert rows.index(next(iter(extra))) < next(i for i,r in enumerate(rows) if '--dst-type LOCAL' in r)


@pytest.mark.parametrize('patch',[
    {'source_cidr':'0.0.0.0/0'}, {'source_cidr':'8.8.8.0/24','listen_address':'8.8.8.1'},
    {'listen_address':'0.0.0.0'}, {'listen_address':'10.11.31.1'},
    {'listen_address':'172.18.255.255'}, {'listen_address':'172.18.0.0'},
    {'interface':'enp3s0'}, {'interface':'br-x;evil'}, {'port':18830},
])
def test_widened_or_malformed_policy_is_rejected(patch):
    with pytest.raises(ValueError):
        container_http_clients({**BASE,'container_http_clients':[{**CLIENT,**patch}]})


def test_non_controller_and_duplicate_addresses_rejected():
    for cfg in ({**BASE,'node':'XZ-31-002','container_http_clients':[CLIENT]},
                {**BASE,'container_http_clients':[CLIENT,CLIENT]}):
        with pytest.raises(ValueError):container_http_clients(cfg)


def test_new_process_only_binds_gateway_and_retains_business_contract():
    template=(Path(__file__).resolve().parents[2]/'deploy/maas/edge.nginx.conf').read_text()
    output=render_nginx(template,{**BASE,'container_http_clients':[CLIENT]})
    assert 'listen 172.18.0.1:18831;' in output
    assert 'listen 127.0.0.1' not in output and 'listen [::1]' not in output
    assert 'allow 172.18.0.0/16;\n        deny all;' in output
    for retained in ('/ocr 1;', '/v1/ocr 1;', 'proxy_set_header Authorization $http_authorization;',
                     'proxy_next_upstream off;', 'if ($maas_path = 0)', 'client_max_body_size 20m;',
                     'proxy_set_header X-Project "";', 'proxy_pass http://127.0.0.1:18830;'):
        assert retained in output
    assert 'listen 127.0.0.1:18831;' in template
