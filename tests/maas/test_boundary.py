"""Policy structure; kernel behavior is separately tested in real network namespaces."""
import importlib.util
from pathlib import Path
spec=importlib.util.spec_from_file_location('boundary',Path(__file__).resolve().parents[2]/'scripts/maas/runtime/boundary.py')
mod=importlib.util.module_from_spec(spec);spec.loader.exec_module(mod)
INV={'ip':'10.11.31.1','interface':'ens18','trusted_bridge':['172.17.0.4'],'destinations':[{'name':'server','ip':'172.17.0.4','port':80}]}


def test_boundary_covers_host_dnat_direct_container_and_ipv6_without_ssh():
    hooks=mod.hooks(INV,4)
    assert any('--ctorigdstport' in r and '18080' in r for r in hooks)
    assert any('--dport' in r and '80' in r and '172.17.0.4/32' in r for r in hooks)
    assert mod.hooks(INV,6)[0][0]=='INPUT'
    assert '22' not in mod.PORTS.split(',')
    assert '40000:40063' in mod.PORTS and '18830' in mod.PORTS


def test_only_explicit_trusted_interfaces_return_before_reject():
    v4=mod.chain_rules(INV,4);v6=mod.chain_rules(INV,6)
    assert not any('ESTABLISHED' in r or 'ACCEPT' in r for r in v4+v6)
    assert v6==['-A MAAS-P3-HOST -i lo -j RETURN','-A MAAS-P3-HOST -p tcp -j REJECT --reject-with tcp-reset']
    for r in v4:
        if '-j RETURN' in r:assert '-i lo ' in r or ('-s ' in r and '-i ' in r)


def test_prior_accept_or_return_invalidates_existing_hooks():
    owned='-A INPUT -p tcp -j MAAS-P3-HOST\n'
    assert mod.hooks_first('-P INPUT ACCEPT\n'+owned,1,mod.HOST)
    assert not mod.hooks_first('-A INPUT -j ACCEPT\n'+owned,1,mod.HOST)
    assert not mod.hooks_first('-A INPUT -j MAAS-P3-HOST-OTHER\n'+owned,1,mod.HOST)
    assert not mod.hooks_first('-A DOCKER-USER -j RETURN\n-A DOCKER-USER -j MAAS-P3-DOCKER',1,mod.DOCKER)


def test_native_exception_cannot_open_other_ports_or_direct_bridge():
    inv={**INV,'node':'XZ-31-001','native_server_cidrs':['10.11.31.0/24']}
    extra=set(mod.chain_rules(inv,4))-set(mod.chain_rules(INV,4))
    assert len(extra)==2
    host=next(x for x in extra if x.startswith('-A '+mod.HOST))
    docker=next(x for x in extra if x.startswith('-A '+mod.DOCKER))
    assert '-d 10.11.31.1/32 -p tcp --dport 18080' in host
    assert '--ctdir ORIGINAL --ctorigdst 10.11.31.1 --ctorigdstport 18080' in docker
    assert all('-i ens18 -s 10.11.31.0/24' in x for x in extra)
    assert mod.chain_rules(inv,6)==mod.chain_rules(INV,6)
