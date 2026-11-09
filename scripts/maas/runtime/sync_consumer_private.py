"""Limit the fixed consumer endpoint to local Docker business bridges."""
import ipaddress
import json
import re
import subprocess
import sys


def run(*args, input=None):
    return subprocess.run(args, input=input, capture_output=True, text=True, check=True, timeout=5).stdout


def business_interfaces():
    ids = run('docker', 'network', 'ls', '--filter', 'driver=bridge', '--quiet').split()
    networks = json.loads(run('docker', 'network', 'inspect', *ids)) if ids else []
    links = {row['ifname'] for row in json.loads(run('ip', '-j', 'link', 'show', 'type', 'bridge'))}
    interfaces = set()
    for network in networks:
        if network['Driver'] != 'bridge' or network['Scope'] != 'local' or network['Internal']:
            continue
        if network['Name'] == 'maas-backend':
            continue
        interface = network['Options'].get('com.docker.network.bridge.name', 'br-' + network['Id'][:12])
        if interface in links and re.fullmatch(r'[a-zA-Z0-9_.-]{1,15}', interface):
            interfaces.add(interface)
    return sorted(interfaces)


def apply(address, interfaces):
    table = 'inet maas_consumer_private'
    rules = [
        f'add table {table}',
        f'flush table {table}',
        f'add chain {table} input {{ type filter hook input priority -10; policy accept; }}',
    ]
    if interfaces:
        members = ', '.join(json.dumps(interface) for interface in interfaces)
        rules.append(f'add rule {table} input ip daddr {address} tcp dport 18831 iifname {{ {members} }} return')
    rules.append(f'add rule {table} input ip daddr {address} tcp dport 18831 counter drop')
    run('nft', '-f', '-', input='\n'.join(rules) + '\n')


if __name__ == '__main__':
    address = ipaddress.IPv4Address(sys.argv[1])
    try:
        interfaces = business_interfaces()
    except (OSError, subprocess.SubprocessError, ValueError, KeyError):
        apply(address, [])
        raise
    apply(address, interfaces)
