"""Read-only Linux host witness for one allowed PP-OCRv4 runtime."""
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


def bind_peer(pid, allowed_image):
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
    if (row['Image'] != allowed_image or [row['Path'], *row['Args']][:2] != ['python3', '/app/server.py']
            or type(state['Pid']) is not int or state['Pid'] != pid or not state['Running']
            or host['Privileged'] or host.get('CapAdd') or host.get('PidMode')
            or any(mount.get('Destination') in ('/proc', '/sys', '/sys/fs/cgroup', '/var/run/docker.sock',
                                                '/run/docker.sock') for mount in row['Mounts'])):
        raise ValueError('Runtime does not meet the PP containment policy')
    path = CGROUP / relative.lstrip('/')
    identity = {'boot': BOOT.read_text().strip(), 'container': container, 'cgroup': relative,
                'inode': path.stat().st_ino, 'started': state['StartedAt'], 'pid': pid}
    if not valid_identity(identity) or not populated(path):
        raise ValueError('Runtime identity unavailable')
    return identity


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


def answer(opener, origin, key, gpu, route, *, reconnected=False):
    offer = exchange(opener, origin, key, {'operation': 'runtime_poll', 'gpu_uuid': gpu,
                                          'reconnected': reconnected})
    if not isinstance(offer['checks'], list) or len(offer['checks']) > 4:
        raise ValueError('Invalid challenge batch')
    for check in offer['checks']:
        if (check['route'] != route or not INSTANCE.fullmatch(check['instance']) or
                not HEX.fullmatch(check['nonce']) or not valid_identity(check['identity'])):
            continue
        try:
            outcome = inspect_original(check['identity'], gpu)
        except (OSError, ValueError, KeyError, subprocess.SubprocessError):
            outcome = 'unconfirmed'
        payload = {**check, 'gpu_uuid': gpu, 'outcome': outcome}
        try:
            exchange(opener, origin, key, {'operation': 'runtime_report', 'gpu_uuid': gpu,
                     'instance': check['instance'], 'nonce': check['nonce'], 'outcome': outcome,
                     'signature': signature(key, 'inspect', payload)})
        except HTTPError as error:
            error.close()
            if error.code != 409:
                raise


def serve(config):
    gpu, route, origin, allowed_image = (config[k] for k in ('gpu_uuid','route','origin','image_id'))
    url = urlsplit(origin)
    path = Path(config['socket'])
    if (sys.platform != 'linux' or url.scheme != 'https' or not url.hostname or url.username or
            url.path or url.query or url.fragment or not GPU.fullmatch(gpu) or not IMAGE.fullmatch(allowed_image)
            or not isinstance(route, str) or not 1 <= len(route) <= 128 or not path.is_absolute()):
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
                identity = bind_peer(pid, allowed_image)
                payload = {'gpu_uuid': gpu, 'instance': instance, 'route': route, 'identity': identity}
                self.wfile.write(json.dumps({'identity': identity, 'signature': signature(key, 'bind', payload)}).encode()+b'\n')
            except (OSError, ValueError, KeyError, subprocess.SubprocessError):
                print('runtime_binding_unavailable', file=sys.stderr, flush=True)

    # The root-owned RuntimeDirectory is the only host directory mounted into PP.
    with UnixStreamServer(str(path), BindingRequest) as binding:
        path.chmod(0o660)
        if 'socket_gid' in config:
            os.chown(path, -1, config['socket_gid'])
        threading.Thread(target=binding.serve_forever, daemon=True).start()
        wake = threading.Event()
        disconnected = True
        while True:
            try:
                answer(opener, origin, key, gpu, route, reconnected=disconnected)
                disconnected = False
            except (OSError, ValueError, URLError, KeyError, TypeError):
                disconnected = True
                print('runtime_poll_unavailable', file=sys.stderr, flush=True)
            wake.wait(5)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', required=True, type=Path)
    serve(json.loads(read_file(parser.parse_args().config, secret=True)))
