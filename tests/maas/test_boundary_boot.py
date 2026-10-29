import pytest
from maas_platform.boundary_policy import rules,CHAIN,TABLE,REPLY_MATCH,native_sources


def test_boot_fence_is_pre_dnat_and_does_not_trust_stale_bridge():
 cfg={'interface':'eth1','bridge_subnets':['172.17.0.0/16']}
 early=rules(cfg,4)
 assert any('--dst-type LOCAL' in x and '-j DROP' in x for x in early)
 assert any('-d 172.17.0.0/16' in x for x in early)
 assert not any('-i docker0' in x for x in early)
 assert any('-i docker0 -s 172.17.0.2/32 -j RETURN' in x for x in rules(cfg,4,['172.17.0.2']))
 assert not any('22,' in x or '--dport 22' in x for x in early)
 assert rules(cfg,6)[0]==f'-A {CHAIN} -i lo -j RETURN'


def test_native_cidrs_only_open_controller_published_port_before_dnat():
 cfg={'node':'XZ-31-001','interface':'eth1','bridge_subnets':['172.17.0.0/16'],
      'native_server_cidrs':['10.11.31.0/24','10.11.32.0/24']}
 exceptions=[x for x in rules(cfg,4) if '--dport 18080' in x]
 assert len(exceptions)==2
 assert all('-i eth1 -s 10.11.' in x and '-d 10.11.31.1/32 -p tcp --dport 18080 -j RETURN' in x for x in exceptions)
 assert not any('--dport 18080' in x for x in rules(cfg,6))
 for bad in ['0.0.0.0/0','::/0','bad']:
  with pytest.raises(ValueError):native_sources({**cfg,'native_server_cidrs':[bad]})
 with pytest.raises(ValueError):native_sources({**cfg,'node':'XZ-31-002'})


def test_only_tracked_reply_direction_bypasses_service_port_fence():
 assert TABLE=='mangle'
 cfg={'interface':'eth1','bridge_subnets':['172.17.0.0/16']}
 for family in (4,6):
  rows=rules(cfg,family)
  reply=[r for r in rows if 'ESTABLISHED' in r]
  assert reply==[f'-A {CHAIN} {REPLY_MATCH} -j RETURN']
  assert '--ctdir REPLY' in reply[0]
  assert rows.index(reply[0]) < next(i for i,r in enumerate(rows) if '-j DROP' in r)
  assert not any('--ctstate RELATED' in r or '--syn' in r for r in rows)
