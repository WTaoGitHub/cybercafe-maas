from maas_platform.boundary_policy import rules,CHAIN


def test_boot_fence_is_pre_dnat_and_does_not_trust_stale_bridge():
 cfg={'interface':'eth1','bridge_subnets':['172.17.0.0/16']}
 early=rules(cfg,4)
 assert any('--dst-type LOCAL' in x and '-j DROP' in x for x in early)
 assert any('-d 172.17.0.0/16' in x for x in early)
 assert not any('-i docker0' in x for x in early)
 assert any('-i docker0 -s 172.17.0.2/32 -j RETURN' in x for x in rules(cfg,4,['172.17.0.2']))
 assert not any('22,' in x or '--dport 22' in x for x in early)
 assert rules(cfg,6)[0]==f'-A {CHAIN} -i lo -j RETURN'
