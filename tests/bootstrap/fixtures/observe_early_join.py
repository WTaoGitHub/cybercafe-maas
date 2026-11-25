"""Loopback control-plane fixture for a disposable Linux cold-boot test.

Run as a service with: python3 observe_early_join.py FIXTURE_DIR RECEIPT
Point the test clone's config and native server URLs at https://127.0.0.1:19445.
FIXTURE_DIR contains a signed test envelope.json, shared test code in shared,
and tls.pem/tls.key valid for loopback. Use a fresh RECEIPT under /run.
Enable the shipped bootstrap timer and a 90-second start_appinstaller.service
ordered before multi-user.target. The first automatic permit request records
both unit states and receives HTTP 409 so no worker credential is issued.
"""
import json
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
import ssl
import subprocess
import sys
import time


def property_of(unit, name):
    return subprocess.check_output(['systemctl', 'show', '-P', name, unit],
                                   text=True, timeout=3).strip()


class Observer(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def reply(self, status, data):
        body = json.dumps(data).encode()
        self.send_response(status)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path == '/healthz':
            self.reply(200, {'ok': True})
        elif self.path == '/version':
            self.reply(200, {'version': 'v2.2.3'})
        elif self.path.startswith('/bootstrap/v1/config?'):
            self.reply(200, self.server.envelope)
        else:
            self.reply(404, {})

    def do_POST(self):
        if self.path != '/bootstrap/v1/worker-start/automatic':
            return self.reply(404, {})
        if self.headers.get('Authorization') != 'Bearer ' + self.server.shared_code:
            return self.reply(403, {})
        try:
            size = int(self.headers.get('Content-Length', '0'))
            if not 0 < size <= 4096:
                return self.reply(400, {})
            request = json.loads(self.rfile.read(size))
        except (ValueError, UnicodeError):
            return self.reply(400, {})
        manifest = self.server.envelope['manifest']
        if (not isinstance(request, dict)
                or request.get('boot_id') != Path('/proc/sys/kernel/random/boot_id').read_text().strip()
                or request.get('image_id') not in manifest['compatible_image_ids']
                or request.get('cluster_id') != manifest['cluster_id']
                or not isinstance(request.get('gpu_uuid'), str) or not request['gpu_uuid'].startswith('GPU-')):
            return self.reply(400, {})
        if not self.server.receipt.exists():
            record = dict(boot_time_seconds=time.clock_gettime(time.CLOCK_BOOTTIME), path=self.path,
                          installer=property_of('start_appinstaller.service', 'ActiveState'),
                          installer_substate=property_of('start_appinstaller.service', 'SubState'),
                          target=property_of('multi-user.target', 'ActiveState'),
                          timer=property_of('maas-worker-bootstrap.timer', 'LastTriggerUSecMonotonic'),
                          naming=property_of('swcfghostname.service', 'Result'),
                          docker=property_of('docker.service', 'ActiveState'),
                          bootstrap=property_of('maas-worker-bootstrap.service', 'ActiveState'))
            self.server.receipt.write_text(json.dumps(record) + '\n')
        self.reply(409, {})


if __name__ == '__main__':
    fixture, receipt = map(Path, sys.argv[1:])
    tls = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    tls.load_cert_chain(fixture/'tls.pem', fixture/'tls.key')
    with HTTPServer(('127.0.0.1', 19445), Observer) as server:
        server.envelope = json.loads((fixture/'envelope.json').read_text())
        server.shared_code = (fixture/'shared').read_text().strip()
        server.receipt = receipt
        server.socket = tls.wrap_socket(server.socket, server_side=True)
        server.serve_forever()
