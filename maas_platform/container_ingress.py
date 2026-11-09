"""Explicit Docker-bridge HTTP access; no blanket trust of a container subnet."""
import ipaddress
import re


def container_http_clients(config):
    rows = config.get('container_http_clients', [])
    if not isinstance(rows, list):
        raise ValueError('container_http_clients must be a list')
    if rows and config.get('node') != 'XZ-31-001':
        raise ValueError('Container HTTP ingress requires controller')
    output, seen = [], set()
    for row in rows:
        interface = row['interface']
        if not isinstance(interface, str) or not re.fullmatch(r'(br-[a-zA-Z0-9_-]{1,12}|docker0)', interface):
            raise ValueError('Explicit Docker bridge interface required')
        network = ipaddress.IPv4Network(row['source_cidr'], strict=True)
        address = ipaddress.IPv4Address(row['listen_address'])
        private = [ipaddress.IPv4Network(n) for n in ('10.0.0.0/8', '172.16.0.0/12', '192.168.0.0/16')]
        if not any(network.subnet_of(n) for n in private):
            raise ValueError('RFC1918 Docker network required')
        if address not in network or address in (network.network_address, network.broadcast_address):
            raise ValueError('Listen address must be a usable address in the Docker network')
        if row.get('port', 18831) != 18831:
            raise ValueError('Only the MaaS HTTP business port is permitted')
        if str(address) in seen:
            raise ValueError('Duplicate ingress address')
        seen.add(str(address))
        output.append({'interface': interface, 'source_cidr': str(network),
                       'listen_address': str(address), 'port': 18831})
    return output


def render_nginx(base, config):
    """Reuse the approved loopback edge's routes/limits; separate bridge-only process."""
    return _render_bridge_listeners(base, container_http_clients(config))


def render_private_consumer_nginx(base, address):
    address = ipaddress.IPv4Address(address)
    if not any(address in ipaddress.IPv4Network(cidr) for cidr in
               ('10.0.0.0/8', '172.16.0.0/12', '192.168.0.0/16')):
        raise ValueError('Consumer service requires a private IPv4 address')
    return _render_bridge_listeners(base, [{'listen_address': str(address)}])


def _render_bridge_listeners(base, rows):
    if not rows:
        raise ValueError('At least one explicit Docker network is required')
    if base.count('    server {') != 1:
        raise ValueError('Expected canonical single-server HTTP template')
    start = base.index('    server {')
    end = base.rfind('\n}')
    server = base[start:end]
    if 'listen 127.0.0.1:18831;' not in server or 'listen [::1]:18831;' not in server:
        raise ValueError('Expected loopback HTTP template')
    servers = []
    for row in rows:
        value = server.replace('listen 127.0.0.1:18831;', 'listen '+row['listen_address']+':18831;')
        value = value.replace('        listen [::1]:18831;\n', '')
        if 'source_cidr' in row:
            value = value.replace('server_name _;', 'server_name _;\n        allow '+row['source_cidr']+';\n        deny all;')
        servers.append(value)
    return base[:start] + '\n'.join(servers) + '\n}\n'
