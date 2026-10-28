import pytest
from maas_bootstrap.clone_boundary import render,validate,PORTS,RAW
CFG={'schema_version':1,'interface':'enp3s0','controllers':['10.11.31.1'],'bridge_subnets':['172.17.0.0/16']}


def test_only_controller_on_lan_and_loopback_are_trusted_not_peer_workers():
 v=render(CFG,4)
 assert v['raw'][0]=='-A '+RAW+' -i lo -j RETURN'
 assert '-i enp3s0 -s 10.11.31.1/32 -j RETURN' in v['raw'][1]
 assert not any('10.11.31.2' in r or '10.11.31.3' in r or 'ESTABLISHED' in r for rows in v.values() for r in rows)
 assert not any('10.11.31.1' in r for rows in render(CFG,6).values() for r in rows)


def test_pre_dnat_and_forward_protect_dynamic_tcp_udp_without_ssh():
 for family in (4,6):
  value=render(CFG,family)
  assert any('--dst-type LOCAL' in r and '-p tcp' in r for r in value['raw'])
  assert any('-p udp' in r for r in value['raw'])
  assert any('40000:40063' in r for r in value['forward'])
 assert '22' not in PORTS.split(',')
 assert any('-d 172.17.0.0/16' in r for r in render(CFG,4)['raw'])


@pytest.mark.parametrize('patch',[{'interface':'lo'},{'interface':'eth0;evil'},{'controllers':['0.0.0.0']},{'controllers':['8.8.8.8']},{'controllers':[]},{'bridge_subnets':['0.0.0.0/0']},{'bridge_subnets':['::/0']}])
def test_invalid_scope_rejected(patch):
 with pytest.raises(ValueError):validate({**CFG,**patch})
