"""Read-only Linux host witness for enrolled execution-guard routes on one GPU."""
import argparse
import json
import os
from pathlib import Path
import re
import socket
from socketserver import StreamRequestHandler, UnixStreamServer
import ssl
import struct
import subprocess
import sys
import threading
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import build_opener, HTTPSHandler, ProxyHandler, Request

from maas_platform.runtime_proof import AUTHORITY_PATH, GPU, HEX, INSTANCE, cgroup_paths, signature, valid_identity
from .client import NoRedirect, read_file
from .execution import PREFIX

CGROUP = Path('/sys/fs/cgroup')
PROC = Path('/proc')
BOOT = PROC / 'sys/kernel/random/boot_id'
IMAGE = re.compile(r'sha256:[0-9a-f]{64}')
MAX_MESSAGE = 16384


def inspect(container):
    result = subprocess.run(['docker', '--host', 'unix:///var/run/docker.sock', 'inspect', '--type', 'container', container],
                            capture_output=True, check=False, timeout=2)
    if result.returncode:
        if result.returncode == 1 and container.encode() in result.stderr and b'No such ' in result.stderr:
            return None
        raise ValueError('Docker inspect unavailable')
    row, = json.loads(result.stdout)
    if row['Id'] != container:
        raise ValueError('Docker container identity mismatch')
    return row


def image_has_digest(image_id, digest):
    if not isinstance(image_id, str) or not IMAGE.fullmatch(image_id):
        return False
    output = subprocess.run(['docker', '--host', 'unix:///var/run/docker.sock', 'image', 'inspect', image_id],
                            capture_output=True, check=True, timeout=2).stdout
    image, = json.loads(output)
    return image['Id'] == image_id and any(ref.rpartition('@')[2] == digest for ref in image.get('RepoDigests') or [])


def check_cgroup_mount():
    mounts = Path('/proc/self/mountinfo').read_text().splitlines()
    for mount in mounts:
        left, _, right = mount.partition(' - ')
        fields = left.split()
        if len(fields) > 4 and fields[4] == str(CGROUP) and fields[3] == '/' and right.startswith('cgroup2 '):
            return
    raise ValueError('Host cgroup v2 root is required')


def populated(path):
    value = dict(line.split() for line in (path / 'cgroup.events').read_text().splitlines())['populated']
    if value not in ('0', '1'):
        raise ValueError('Invalid cgroup population')
    return value == '1'


def bind_peer(pid, routes):
    check_cgroup_mount()
    hierarchy, relative = (PROC / str(pid) / 'cgroup').read_text().strip().split('::')
    if hierarchy != '0':
        raise ValueError('Host cgroup v2 is required')
    container = relative.removeprefix('/system.slice/docker-').removesuffix('.scope')
    if relative.startswith('/docker/'):
        container = relative.removeprefix('/docker/')
    if relative not in cgroup_paths(container):
        raise ValueError('Unsupported container cgroup')
    row = inspect(container)
    if row is None:
        raise ValueError('Container identity unavailable')
    host = row['HostConfig']
    state = row['State']
    declared = [v.partition('=') for v in row['Config']['Env']
                if v.partition('=')[0].endswith('ROUTE') and PREFIX.fullmatch(v.partition('=')[0][:-5])]
    if len(declared) != 1 or declared[0][2] not in routes:
        raise ValueError('Runtime does not meet the route containment policy')
    variable, _, name = declared[0]
    rule = routes[name]
    init_pid = state['Pid']
    if (type(init_pid) is not int or init_pid <= 0 or not state['Running']
            or host['Privileged'] or host.get('CapAdd') or host.get('PidMode')
            or any(mount.get('Destination') in ('/proc', '/sys', '/sys/fs/cgroup', '/var/run/docker.sock',
                                                '/run/docker.sock') for mount in row['Mounts'])):
        raise ValueError('Runtime does not meet the route containment policy')
    command = [row['Path'], *row['Args']]
    if isinstance(rule, str):
        matched = (variable == 'MAAS_PP_OCR_EXECUTION_ROUTE' and rule == row['Image']
                   and command[:2] == ['python3', '/app/server.py'] and init_pid == pid)
    else:
        entry = rule['entrypoint']
        matched = (variable == rule['execution_env_prefix'] + 'ROUTE' and command[:len(entry)] == entry
                   and row['Config']['Entrypoint'] == entry and image_has_digest(row['Image'], rule['digest']))
        if matched and init_pid != pid:
            # A frontend child may own the socket. Bind the whole original container,
            # including its engine children, and never just the short-lived frontend.
            matched = (PROC / str(init_pid) / 'cgroup').read_text().strip() == '0::' + relative
    if not matched:
        raise ValueError('Runtime does not meet the route containment policy')
    path = CGROUP / relative.lstrip('/')
    identity = {'boot': BOOT.read_text().strip(), 'container': container, 'cgroup': relative,
                'inode': path.stat().st_ino, 'started': state['StartedAt'], 'pid': init_pid}
    if not valid_identity(identity) or not populated(path):
        raise ValueError('Runtime identity unavailable')
    return name, identity


def original_gpu_released(gpu, pid):
    output = subprocess.run(['nvidia-smi', '-i', gpu, '--query-compute-apps=pid',
                             '--format=csv,noheader,nounits'], capture_output=True, check=True, timeout=2).stdout
    pids = output.split()
    if any(not value.isdigit() for value in pids):
        raise ValueError('Compute process inventory unavailable')
    return str(pid).encode() not in pids


def inspect_original(identity, gpu):
    if not valid_identity(identity) or identity['boot'] != BOOT.read_text().strip():
        return 'unconfirmed'
    check_cgroup_mount()
    row = inspect(identity['container'])
    path = CGROUP / identity['cgroup'].lstrip('/')
    try:
        inode = path.stat().st_ino
    except FileNotFoundError:
        # Under the same boot, the kernel cannot remove a populated original cgroup.
        # A successful full-ID lookup must also agree that the original run is gone.
        if row is None or (row['State']['StartedAt'] == identity['started'] and
                           row['State']['Status'] in ('exited', 'dead') and row['State']['Pid'] == 0):
            return 'terminated' if original_gpu_released(gpu, identity['pid']) else 'unconfirmed'
        return 'unconfirmed'
    if inode != identity['inode'] or row is None or row['State']['StartedAt'] != identity['started']:
        return 'unconfirmed'
    if populated(path):
        return 'present'
    if row['State']['Status'] in ('exited', 'dead') and row['State']['Pid'] == 0:
        return 'terminated' if original_gpu_released(gpu, identity['pid']) else 'unconfirmed'
    return 'unconfirmed'


def exchange(opener, origin, key, payload):
    request = Request(origin + AUTHORITY_PATH, method='POST',
                      headers={'Authorization': 'Bearer '+key, 'Content-Type': 'application/json'},
                      data=json.dumps(payload).encode())
    with opener.open(request, timeout=3) as response:
        data = response.read(MAX_MESSAGE+1)
    if len(data) > MAX_MESSAGE:
        raise ValueError('Response exceeds limit')
    return json.loads(data) if data else None


def answer(opener, origin, key, gpu, routes, *, reconnected=False, boot=None):
    scope = {'boot_id': boot} if boot is not None else {}
    offer = exchange(opener, origin, key, {**scope, 'operation': 'runtime_poll', 'gpu_uuid': gpu,
                                          'reconnected': reconnected})
    if not isinstance(offer['checks'], list) or len(offer['checks']) > 4:
        raise ValueError('Invalid challenge batch')
    for check in offer['checks']:
        if (check['route'] not in routes or not INSTANCE.fullmatch(check['instance']) or
                not HEX.fullmatch(check['nonce']) or not valid_identity(check['identity'])):
            continue
        try:
            outcome = inspect_original(check['identity'], gpu)
        except (OSError, ValueError, KeyError, subprocess.SubprocessError):
            outcome = 'unconfirmed'
        payload = {**check, 'gpu_uuid': gpu, 'outcome': outcome}
        try:
            exchange(opener, origin, key, {**scope, 'operation': 'runtime_report', 'gpu_uuid': gpu,
                     'instance': check['instance'], 'nonce': check['nonce'], 'outcome': outcome,
                     'signature': signature(key, 'inspect', payload)})
        except HTTPError as error:
            error.close()
            if error.code != 409:
                raise


def enrolled_routes(config):
    if 'routes' in config:
        if 'route' in config or 'image_id' in config:
            raise ValueError('Conflicting route configurations')
        routes = config['routes']
    else:
        routes = {config['route']: config['image_id']}
    if not isinstance(routes, dict) or not routes:
        raise ValueError('Invalid witness routes')
    for name, rule in routes.items():
        if not isinstance(name, str) or not 1 <= len(name) <= 128 or not name.isprintable():
            raise ValueError('Invalid witness route name')
        if isinstance(rule, str) and IMAGE.fullmatch(rule):
            continue
        if (not isinstance(rule, dict) or set(rule) != {'digest', 'entrypoint', 'execution_env_prefix'}
                or not isinstance(rule['digest'], str) or not IMAGE.fullmatch(rule['digest'])
                or not isinstance(rule['execution_env_prefix'], str) or not PREFIX.fullmatch(rule['execution_env_prefix'])
                or not isinstance(rule['entrypoint'], list) or not 1 <= len(rule['entrypoint']) <= 16
                or not all(isinstance(s, str) and 1 <= len(s) <= 256 and s.isprintable() for s in rule['entrypoint'])):
            raise ValueError('Invalid witness image rule')
    return routes


def current_routes(config):
    if 'release_routes_file' not in config:
        return enrolled_routes(config)
    released = json.loads(read_file(Path(config['release_routes_file']), secret=True))
    fixed = enrolled_routes(config) if any(k in config for k in ('route', 'routes', 'image_id')) else {}
    if released == {}:
        return fixed
    return {**fixed, **enrolled_routes({'routes': released})}


def serve(config):
    gpu, origin = config['gpu_uuid'], config['origin']
    current_routes(config)
    if config.get('boot_id') is not None and config['boot_id'] != BOOT.read_text().strip():
        raise ValueError('Witness enrollment belongs to another boot')
    url = urlsplit(origin)
    path = Path(config['socket'])
    if (sys.platform != 'linux' or url.scheme != 'https' or not url.hostname or url.username or
            url.path or url.query or url.fragment or not GPU.fullmatch(gpu) or not path.is_absolute()):
        raise ValueError('Invalid witness configuration')
    key = read_file(Path(config['key_file']), secret=True).decode().strip()
    if not HEX.fullmatch(key):
        raise ValueError('Invalid host-only key')
    opener = build_opener(ProxyHandler({}), NoRedirect(), HTTPSHandler(
        context=ssl.create_default_context(cafile=config['ca_file'])))

    class BindingRequest(StreamRequestHandler):
        def handle(self):
            self.request.settimeout(2)
            try:
                instance = self.rfile.readline(35).decode().strip()
                if not INSTANCE.fullmatch(instance):
                    return
                pid, _, _ = struct.unpack('3i', self.request.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12))
                route, identity = bind_peer(pid, current_routes(config))
                payload = {'gpu_uuid': gpu, 'instance': instance, 'route': route, 'identity': identity}
                self.wfile.write(json.dumps({'identity': identity, 'signature': signature(key, 'bind', payload)}).encode()+b'\n')
            except (OSError, ValueError, KeyError, subprocess.SubprocessError):
                print('runtime_binding_unavailable', file=sys.stderr, flush=True)

    # The socket is shared with signed images. Every bind authenticates the actual peer.
    with UnixStreamServer(str(path), BindingRequest) as binding:
        path.chmod(0o666 if 'release_routes_file' in config else 0o660)
        if 'socket_gid' in config:
            os.chown(path, -1, config['socket_gid'])
        threading.Thread(target=binding.serve_forever, daemon=True).start()
        if endpoint := os.getenv('NOTIFY_SOCKET'):
            with socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM) as ready:
                ready.sendto(b'READY=1', '\0' + endpoint[1:] if endpoint.startswith('@') else endpoint)
        wake = threading.Event()
        disconnected = True
        while True:
            try:
                answer(opener, origin, key, gpu, current_routes(config), reconnected=disconnected, boot=config.get('boot_id'))
                disconnected = False
            except (OSError, ValueError, URLError, KeyError, TypeError):
                disconnected = True
                print('runtime_poll_unavailable', file=sys.stderr, flush=True)
            wake.wait(5)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', required=True, type=Path)
    parser.add_argument('--bootstrap-config', type=Path)
    args = parser.parse_args()
    path = args.config
    if args.bootstrap_config and args.bootstrap_config.exists():
        from .client import LocalSettings
        from .execution import local_directory
        managed = local_directory(LocalSettings(args.bootstrap_config)) / 'witness.json'
        if managed.exists():
            path = managed
    serve(json.loads(read_file(path, secret=True)))
