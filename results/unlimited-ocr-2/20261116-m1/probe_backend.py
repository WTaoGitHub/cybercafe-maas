"""Bounded CPU-only HTTP fixture, derived from scripts/maas/p0/probe_backend.py."""
import argparse
import collections
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import socket
import threading
import time

parser = argparse.ArgumentParser()
parser.add_argument('--port', type=int, required=True)
parser.add_argument('--model', required=True)
args = parser.parse_args()
counts = collections.Counter()
framing = collections.Counter()
lock = threading.Lock()


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def setup(self):
        super().setup()
        self.connection.settimeout(5)

    def send(self, status, value):
        data = json.dumps(value).encode()
        self.send_response(status)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        if self.path == '/health':
            return self.send(200, {'ok': True})
        with lock:
            snapshot = dict(counts)
            wire = dict(framing)
        return self.send(200, {'counts': snapshot, 'framing': wire})

    def do_POST(self):
        try:
            encoding = self.headers.get('Transfer-Encoding', '').lower().strip()
            with lock:
                framing[encoding or 'content-length'] += 1
            if encoding == 'chunked':
                data = bytearray()
                for _ in range(1024):
                    line = self.rfile.readline(128)
                    size = int(line.split(b';', 1)[0], 16)
                    if size < 0 or len(data) + size > 1048576:
                        return self.send(413, {'error': 'body limit'})
                    if size == 0:
                        if self.rfile.readline(8192) != b'\r\n':
                            raise ValueError('unexpected trailers')
                        break
                    chunk = self.rfile.read(size)
                    if len(chunk) != size or self.rfile.read(2) != b'\r\n':
                        raise ValueError('invalid chunk')
                    data.extend(chunk)
                else:
                    raise ValueError('chunk limit')
            elif not encoding:
                size = int(self.headers.get('Content-Length', '0'))
                if not 0 <= size <= 1048576:
                    return self.send(413, {'error': 'body limit'})
                data = self.rfile.read(size)
            else:
                raise ValueError('unsupported transfer encoding')
            body = json.loads(data)
            case = body['probe_case']
            mode = body.get('probe_mode', 'normal')
            if not isinstance(case, str) or len(case) > 64:
                raise ValueError('case limit')
        except (ValueError, KeyError, TypeError):
            return self.send(400, {'error': 'invalid fixture request'})
        with lock:
            if case not in counts and len(counts) >= 32:
                return self.send(429, {'error': 'case limit'})
            counts[case] += 1
        if mode in ('503', '502'):
            return self.send(int(mode), {'error': {'message': 'synthetic failure'}})
        if mode == 'reset':
            self.close_connection = True
            self.connection.shutdown(socket.SHUT_RDWR)
            return
        if mode == 'partial':
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', '1000')
            self.end_headers()
            self.wfile.write(b'{"choices":[')
            self.wfile.flush()
            self.close_connection = True
            return
        if mode == 'delay':
            time.sleep(2)
        return self.send(200, {'id': 'm1-fixture', 'model': args.model,
            'choices': [{'index': 0, 'finish_reason': 'stop',
                         'message': {'role': 'assistant', 'content': 'synthetic fixture'}}],
            'usage': {'prompt_tokens': 0, 'completion_tokens': 0, 'total_tokens': 0}})


server = ThreadingHTTPServer(('0.0.0.0', args.port), Handler)
server.daemon_threads = True
timer = threading.Timer(300, server.shutdown)
timer.daemon = True
timer.start()
try:
    server.serve_forever()
finally:
    timer.cancel()
    server.server_close()
