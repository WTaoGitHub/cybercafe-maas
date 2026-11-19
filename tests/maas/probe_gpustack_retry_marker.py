"""Verify request-marker propagation using one disposable GPUStack provider route."""
import argparse
from http.cookiejar import CookieJar
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import ipaddress
import json
from pathlib import Path
import re
import secrets
import signal
import subprocess
from threading import Thread
import time
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import HTTPRedirectHandler, HTTPCookieProcessor, ProxyHandler, Request, build_opener

MARKER = 'X-MaaS-Execution-Attempt'
PROVIDER = '/v2/model-providers'
ROUTE = '/v2/model-routes'
KEY = '/v2/api-keys'


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def drain_probe_body(handler):
    handler.connection.settimeout(3)
    if handler.headers.get('Transfer-Encoding', '').lower() == 'chunked':
        remaining = 4096
        while True:
            length = int(handler.rfile.readline(128).split(b';', 1)[0].strip(), 16)
            if not 0 <= length <= remaining:
                raise ValueError('Probe request chunk exceeds its budget')
            chunk = handler.rfile.read(length + 2)
            if len(chunk) != length + 2 or not chunk.endswith(b'\r\n'):
                raise ValueError('Invalid probe request chunk')
            if not length:
                return
            remaining -= length
    else:
        length = int(handler.headers.get('Content-Length', '0'))
        if not 0 <= length <= 4096 or len(handler.rfile.read(length)) != length:
            raise ValueError('Invalid probe request length')


def run(gateway, listen_host, password, name, gateway_container=None, envoy_admin=None):
    if not ipaddress.IPv4Address(listen_host).is_private:
        raise ValueError('Bind the fixture only on a private interface reachable by Higress')
    if not re.fullmatch(r'test_agent_retry_[0-9a-f]{12}', name):
        raise ValueError('Use a unique test_agent_retry_ fixture name')
    upstream_key = secrets.token_hex(32)
    test_header = 'sync.' + secrets.token_hex(16)
    received = []
    rejected = []
    result = {'fixture': name}
    control = build_opener(ProxyHandler({}), HTTPCookieProcessor(CookieJar()), NoRedirect())
    ingress = build_opener(ProxyHandler({}), NoRedirect())

    def request(path, method='GET', data=None, headers=None, opener=control):
        req = Request(gateway.rstrip('/') + path, method=method, data=data, headers=headers or {})
        try:
            with opener.open(req, timeout=4) as response:
                return response.status, response.read()
        except HTTPError as error:
            error.close()
            return error.code, b''

    def admin(path, method='GET', body=None):
        status, data = request(path, method, json.dumps(body).encode() if body is not None else None,
                               {'Content-Type': 'application/json'})
        if not 200 <= status < 300:
            raise RuntimeError(f'GPUStack {method} {path.split("?")[0]} returned {status}')
        return json.loads(data) if data else None

    class Upstream(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(204 if self.path == '/' + name else 404)
            self.end_headers()

        def do_POST(self):
            if not self.path.endswith('/chat/completions'):
                rejected.append('path')
                self.send_error(404)
                return
            if self.headers.get('Authorization') != 'Bearer ' + upstream_key:
                rejected.append('authorization')
                self.send_error(403)
                return
            try:
                drain_probe_body(self)
            except (ValueError, OSError):
                rejected.append('body_transfer')
                self.send_error(400)
                return
            received.append(self.headers.get(MARKER))
            self.send_response(503)
            self.send_header('Content-Length', '0')
            self.end_headers()

        def log_message(self, *_):
            pass

    status, _ = request('/auth/login', 'POST', urlencode({'username': 'admin', 'password': password}).encode(),
                        {'Content-Type': 'application/x-www-form-urlencoded'})
    if status != 200 or not admin('/v2/users/me')['is_admin']:
        raise RuntimeError(f'GPUStack maintenance session unavailable ({status})')

    with ThreadingHTTPServer((listen_host, 0), Upstream) as server:
        port = server.server_port
        thread = Thread(target=server.serve_forever, daemon=True)
        thread.start()
        paths = []
        try:
            if gateway_container:
                reach = subprocess.run(['docker', 'exec', gateway_container, 'python', '-c',
                    'import sys,urllib.request; assert urllib.request.urlopen(sys.argv[1],timeout=3).status==204',
                    f'http://{listen_host}:{port}/{name}'], capture_output=True, timeout=6)
                result['backend_reachable_from_gateway'] = reach.returncode == 0
                if not result['backend_reachable_from_gateway']:
                    raise RuntimeError('The gateway container cannot reach the temporary backend listener')
            paths.append(PROVIDER)
            provider = admin(PROVIDER, 'POST', {'name': name, 'timeout': 10, 'config': {
                'type': 'openai', 'openaiCustomUrl': f'http://{listen_host}:{port}/v1'},
                'api_tokens': [{'input': upstream_key}], 'models': [{'name': name, 'category': 'llm'}]})
            paths.append(ROUTE)
            admin(ROUTE, 'POST', {'name': name, 'categories': ['llm'], 'targets': [
                {'provider_id': provider['id'], 'overridden_model_name': name, 'weight': 100}]})
            paths.append(KEY)
            key = admin(KEY, 'POST', {'name': name, 'scope': ['inference'], 'allowed_model_names': [name], 'expires_in': 180})
            body = json.dumps({'model': name, 'messages': [{'role': 'user', 'content': 'test_agent'}], 'stream': False}).encode()
            statuses = []
            for _ in range(6):
                code, _ = request('/v1/chat/completions', 'POST', body, {
                    'Content-Type': 'application/json', 'Authorization': 'Bearer ' + key['value'], MARKER: test_header}, ingress)
                statuses.append(code)
                if received or rejected:
                    break
                time.sleep(1)
            result['gateway_statuses'] = statuses
            result.update(http_status=code, upstream_calls=len(received), marker_preserved=bool(received) and all(
                header == test_header for header in received))
            if envoy_admin:
                with ingress.open(envoy_admin + '/config_dump', timeout=4) as response:
                    dump = json.load(response)
                route_configs = (route for item in dump['configs'] for section in ('dynamic_route_configs', 'static_route_configs')
                                 for config in item.get(section, []) for host in config.get('route_config', {}).get('virtual_hosts', [])
                                 for route in host.get('routes', []) if name in json.dumps(route.get('match', {})))
                result['configured_retries'] = sorted({route.get('route', {}).get('retry_policy', {}).get('num_retries', -1)
                                                       for route in route_configs})
            if (code, len(received), result['marker_preserved']) != (503, 3, True):
                raise RuntimeError('Gateway did not produce three 503 attempts with an identical marker')
        finally:
            server.shutdown()
            thread.join()
            server.server_close()
            cleanup_errors = []
            search = '?' + urlencode({'search': name, 'perPage': 100})
            for path in reversed(paths):
                try:
                    for item in admin(path + search)['items']:
                        if item['name'] == name:
                            admin(path + '/' + str(item['id']), 'DELETE')
                    if any(item['name'] == name for item in admin(path + search)['items']):
                        cleanup_errors.append(path)
                except RuntimeError:
                    cleanup_errors.append(path)
            result['cleanup_errors'] = cleanup_errors
            result['rejected_upstream_calls'] = rejected
            print(json.dumps(result), flush=True)
            if cleanup_errors:
                raise RuntimeError('Temporary GPUStack resources still require cleanup')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--gateway', required=True)
    parser.add_argument('--listen-host', required=True)
    parser.add_argument('--admin-password-file', required=True, type=Path)
    parser.add_argument('--fixture', required=True)
    parser.add_argument('--gateway-container')
    parser.add_argument('--envoy-admin')
    args = parser.parse_args()

    def interrupted(*_):
        raise KeyboardInterrupt

    signal.signal(signal.SIGHUP, interrupted)
    signal.signal(signal.SIGTERM, interrupted)
    if args.admin_password_file.stat().st_mode & 0o077:
        raise ValueError('Supply an existing private GPUStack admin password file')
    run(args.gateway, args.listen_host, args.admin_password_file.read_text().strip(), args.fixture,
        args.gateway_container, args.envoy_admin)


if __name__ == '__main__':
    main()
