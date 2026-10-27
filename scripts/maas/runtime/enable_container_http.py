"""001-only, explicit Docker-bridge MaaS HTTP ingress. Never restarts existing services.

plan: inspect network and render candidate without writing.
apply: validate nginx, persist narrow pre-DNAT exception and start a separate ingress.
Existing loopback edges and API/executor/worker/database processes are untouched.
"""
import argparse
import fcntl
import hashlib
import ipaddress
import json
import os
from pathlib import Path
import re
import shlex
import socket
import subprocess
import sys
import tempfile
import time

FRAME = Path('/root/wtaobj/maas-framework')
ROOT = Path('/root/wtaobj/maas-container-http')
POLICY = Path('/root/wtaobj/maas-boundary/persistent.json')
UNIT = Path('/etc/systemd/system/maas-container-http.service')
NAME = 'maas-container-http'
sys.path[:0] = [str(FRAME), str(FRAME/'scripts/maas/runtime')]
from maas_platform.container_ingress import container_http_clients, render_nginx
import persist_boundary


def run(*args, check=True):
    return subprocess.run(args, capture_output=True, text=True, check=check, timeout=35)


def inspect(name):
    return json.loads(run('docker', 'inspect', name).stdout)[0]


def write(path, data, mode):
    fd, tmp = tempfile.mkstemp(prefix='.ingress-', dir=path.parent)
    try:
        with os.fdopen(fd, 'wb') as f:
            os.fchmod(f.fileno(), mode)
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('action', choices=['plan', 'apply'])
    p.add_argument('--network', required=True)
    p.add_argument('--expected-policy-sha256')
    args = p.parse_args()
    if os.getuid() != 0 or socket.gethostname() != 'XZ-31-001':
        raise RuntimeError('001 root only')
    if not re.fullmatch(r'[a-zA-Z0-9][a-zA-Z0-9_.-]*', args.network):
        raise ValueError('Invalid network name')
    network = json.loads(run('docker', 'network', 'inspect', args.network).stdout)[0]
    if network['Driver'] != 'bridge':
        raise ValueError('Only explicit local Docker bridge networks are supported')
    ipv4 = [r for r in network['IPAM']['Config'] if ':' not in r.get('Subnet', '')]
    if len(ipv4) != 1:
        raise ValueError('Expected exactly one IPv4 subnet and gateway')
    row = {'network_name': args.network,
           'interface': network['Options'].get('com.docker.network.bridge.name', 'br-'+network['Id'][:12]),
           'source_cidr': ipv4[0]['Subnet'], 'listen_address': ipv4[0]['Gateway'], 'port': 18831}
    addresses = json.loads(run('ip', '-j', 'address', 'show', 'dev', row['interface']).stdout)
    if not any(a.get('local') == row['listen_address'] for dev in addresses for a in dev['addr_info']):
        raise RuntimeError('Bridge gateway address is not installed')
    fd = os.open('/run/maas-persistent-boundary.lock', os.O_CREAT | os.O_RDWR, 0o600)
    with os.fdopen(fd, 'w') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        original = POLICY.read_bytes()
        policy = json.loads(original)
        policy['container_http_clients'] = [r for r in policy.get('container_http_clients', [])
                                            if r.get('network_name') != args.network] + [row]
        container_http_clients(policy)
        template = (FRAME/'deploy/maas/edge.nginx.conf').read_text()
        nginx = render_nginx(template, policy).encode()
        image = inspect('maas-edge')['Image']
        command = ['docker', 'run', '--rm', '--name', NAME, '--label', 'cybercafe.role=maas-container-http',
                   '--network', 'host', '--read-only', '--user', 'nginx', '--cap-drop=ALL',
                   '--security-opt=no-new-privileges', '--tmpfs', '/tmp:rw,nosuid,nodev,size=64m',
                   '-v', str(ROOT)+':/maas-ingress:ro', '--entrypoint', 'nginx', image,
                   '-c', '/maas-ingress/nginx.conf', '-g', 'daemon off;']
        command[0] = '/usr/bin/docker'
        unit = ('# Managed by cybercafe-maas enable_container_http.py\n[Unit]\n'
                'Description=MaaS HTTP for explicitly allowed Docker bridges\n'
                'After=docker.service maas-boundary-boot.service\nRequires=docker.service\n'
                'StartLimitIntervalSec=0\n[Service]\nRestart=on-failure\nRestartSec=3\n'
                'ExecStart='+shlex.join(command)+'\nExecStop=/usr/bin/docker stop -t 10 '+NAME+'\n'
                'TimeoutStopSec=20\n[Install]\nWantedBy=multi-user.target\n').encode()
        existed = UNIT.exists()
        if existed and UNIT.read_bytes() != unit:
            raise RuntimeError('Unit differs; review instead of replacing another service')
        result = {'action': args.action, 'network': row, 'image_id': image,
                  'nginx_sha256': hashlib.sha256(nginx).hexdigest(),
                  'policy_before_sha256': hashlib.sha256(original).hexdigest(),
                  'public_listener_added': False, 'authentication_required': True}
        if args.action == 'plan':
            print(json.dumps(result)); return
        if not args.expected_policy_sha256 or hashlib.sha256(original).hexdigest() != args.expected_policy_sha256:
            raise RuntimeError('Policy changed since local backup; inspect before applying')
        server = inspect('wta-gpustack-server')
        if server['Image'] != policy['server_image_id']:
            raise RuntimeError('GPUStack server image differs from persistent boundary baseline')
        trusted = []
        for net in server['NetworkSettings']['Networks'].values():
            if net.get('GlobalIPv6Address'):
                raise RuntimeError('Unexpected server IPv6 network')
            if net.get('IPAddress'):
                trusted.append(str(ipaddress.IPv4Address(net['IPAddress'])))
        cfg = ROOT/'nginx.conf'
        old_cfg = cfg.read_bytes() if cfg.exists() else None
        boot = Path('/proc/sys/kernel/random/boot_id').read_text()
        docker_pid = run('systemctl', 'show', 'docker', '-p', 'MainPID', '--value').stdout
        before = {c['Name']: (c['Id'], c['State']['StartedAt'])
                  for c in json.loads(run('docker', 'inspect', *run('docker','ps','-q').stdout.split()).stdout)}
        ROOT.mkdir(mode=0o755, exist_ok=True)
        try:
            write(cfg, nginx, 0o644)
            run('docker','run','--rm','--network','host','--read-only','--user','nginx','--cap-drop=ALL',
                '--security-opt=no-new-privileges','--tmpfs','/tmp:rw,nosuid,nodev,size=16m',
                '-v',str(ROOT)+':/maas-ingress:ro','--entrypoint','nginx',image,
                '-t','-c','/maas-ingress/nginx.conf')
            write(POLICY, (json.dumps(policy, indent=2)+'\n').encode(), 0o600)
            persist_boundary.apply_raw(policy, trusted)
            if not existed:
                write(UNIT, unit, 0o644)
                run('systemctl', 'daemon-reload')
                run('systemctl', 'enable', '--now', NAME+'.service')
            else:
                run('docker', 'exec', NAME, 'nginx', '-t', '-c', '/maas-ingress/nginx.conf')
                run('docker', 'exec', NAME, 'nginx', '-s', 'reload', '-c', '/maas-ingress/nginx.conf')
            for _ in range(20):
                r=run('docker','inspect',NAME,check=False)
                if r.returncode==0 and json.loads(r.stdout)[0]['State']['Running']:
                    break
                time.sleep(.25)
            else:
                raise RuntimeError('New ingress container did not start')
            for name, (ident, started) in before.items():
                current=inspect(name)
                if (current['Id'],current['State']['StartedAt']) != (ident,started):
                    raise RuntimeError('An existing container changed during rollout')
            if boot != Path('/proc/sys/kernel/random/boot_id').read_text() or docker_pid != run('systemctl','show','docker','-p','MainPID','--value').stdout:
                raise RuntimeError('Host/Docker identity changed during rollout')
        except Exception:
            write(POLICY, original, 0o600)
            persist_boundary.apply_raw(json.loads(original), trusted)
            if not existed:
                run('systemctl', 'disable', '--now', NAME+'.service',check=False)
                if UNIT.exists(): UNIT.unlink()
                run('systemctl','daemon-reload',check=False)
            if old_cfg is not None:
                write(cfg,old_cfg,0o644)
                if existed:run('docker','exec',NAME,'nginx','-s','reload','-c','/maas-ingress/nginx.conf',check=False)
            raise
        result.update(existing_containers_unchanged=True, vm_boot_unchanged=True, docker_pid_unchanged=True,
                      container_id=inspect(NAME)['Id'], policy_persisted=True)
        print(json.dumps(result))


if __name__ == '__main__':
    main()
