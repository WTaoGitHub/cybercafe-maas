"""Pre-DNAT boot fence, installed before Docker; only our raw chain is managed."""
CHAIN='MAAS-P4-EARLY'
PORTS='8820,10150:10151,10161,11435,18080,18830:18835,40000:40063,41000:41999'


def rules(config,family,trusted_bridge=()):
    out=[f'-A {CHAIN} -i lo -j RETURN']
    if family==4:
        for address in ('10.11.31.1','10.11.31.2'):out.append(f'-A {CHAIN} -i {config["interface"]} -s {address}/32 -j RETURN')
        for address in trusted_bridge:out.append(f'-A {CHAIN} -i docker0 -s {address}/32 -j RETURN')
    out.append(f'-A {CHAIN} -m addrtype --dst-type LOCAL -p tcp -m multiport --dports {PORTS} -j DROP')
    if family==4:
        for cidr in config['bridge_subnets']:out.append(f'-A {CHAIN} -d {cidr} -p tcp -m multiport --dports 80,8820,11435,40000:40063 -j DROP')
    return out
