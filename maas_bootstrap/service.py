"""Read-only bootstrap configuration API. Private files are mounted read-only.

No node registration, sessions, worker creation or remote shell execution.
"""
import hashlib
import hmac
import ipaddress
import json
import os
import re
import threading
import time
from collections import OrderedDict
from pathlib import Path
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

REVISION = re.compile(r'[a-zA-Z0-9][a-zA-Z0-9_.-]{0,79}\Z')
NO_CACHE = {'Cache-Control': 'no-store', 'Pragma': 'no-cache', 'X-Content-Type-Options': 'nosniff'}


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False).encode()


class AccessFailure(Exception):
    def __init__(self, status, code):
        self.status, self.code = status, code


class SharedCodeAuthenticator:
    """Replace this interface for stronger credentials without changing ReleaseStore."""
    def authenticate(self, header, grants, now):
        if not header or not header.startswith('Bearer '):
            raise AccessFailure(401, 'AUTH_REQUIRED')
        token = header[7:]
        if len(token) > 512 or len(token) < 24:
            raise AccessFailure(401, 'AUTH_INVALID')
        digest = hashlib.sha256(token.encode()).hexdigest()
        grant = next((g for g in grants if hmac.compare_digest(g['code_sha256'], digest)), None)
        if grant is None:
            raise AccessFailure(401, 'AUTH_INVALID')
        if not grant.get('enabled') or (grant.get('expires_at') is not None and now >= grant['expires_at']):
            raise AccessFailure(403, 'AUTH_DISABLED_OR_EXPIRED')
        return grant


class BoundedLimiter:
    def __init__(self, limit=60, seconds=60, capacity=4096):
        self.limit, self.seconds, self.capacity = limit, seconds, capacity
        self.rows = OrderedDict()
        self.lock = threading.Lock()

    def allow(self, key):
        now = time.monotonic()
        with self.lock:
            start, count = self.rows.pop(key, (now, 0))
            if now - start >= self.seconds:
                start, count = now, 0
            self.rows[key] = (start, count + 1)
            while len(self.rows) > self.capacity:
                self.rows.popitem(last=False)
            return count < self.limit


class ReleaseStore:
    def __init__(self, root):
        self.root = Path(root)

    def policy(self):
        p = json.loads((self.root / 'policy.json').read_text())
        if p.get('schema_version') != 1 or not isinstance(p.get('grants'), list):
            raise ValueError('Invalid policy')
        return p

    def release(self, grant, revision=None):
        pointer = json.loads((self.root / 'active.json').read_text())
        rev = pointer['revision']
        if not isinstance(rev, str) or not REVISION.fullmatch(rev):
            raise ValueError('Invalid release pointer')
        # Historical downloads are intentionally restricted to the currently published revision.
        if revision is not None and revision != rev:
            raise AccessFailure(404, 'REVISION_NOT_ACTIVE')
        raw = (self.root / 'releases' / (rev + '.json')).read_bytes()
        if len(raw) > 1024 * 1024 or not hmac.compare_digest(hashlib.sha256(raw).hexdigest(), pointer['sha256']):
            raise ValueError('Release integrity failure')
        envelope = json.loads(raw)
        m = envelope['manifest']
        if m['schema_version'] != 1 or m['config_revision'] != rev:
            raise ValueError('Invalid manifest')
        if m['site_id'] != grant['site_id'] or m['batch_id'] != grant['batch_id']:
            raise AccessFailure(403, 'SCOPE_MISMATCH')
        if m['cluster_id'] != grant['cluster_id'] or envelope['registration']['cluster_id'] != m['cluster_id']:
            raise AccessFailure(403, 'CLUSTER_MISMATCH')
        if not envelope['registration'].get('token') or envelope['manifest_signature'].get('algorithm') != 'Ed25519':
            raise ValueError('Invalid protected registration or signature')
        if not isinstance(m.get('compatible_image_ids'), list) or not m['compatible_image_ids']:
            raise ValueError('Invalid compatibility list')
        return envelope


def create_app(state_dir=None, authenticator=None, limiter=None):
    store = ReleaseStore(state_dir or os.environ['MAAS_BOOTSTRAP_STATE'])
    auth = authenticator or SharedCodeAuthenticator()
    throttle = limiter or BoundedLimiter()
    app = FastAPI(title='MaaS bootstrap configuration', docs_url=None, redoc_url=None, openapi_url=None)

    def reply(status, code):
        return JSONResponse({'error': {'code': code}}, status_code=status, headers=NO_CACHE)

    @app.middleware('http')
    async def source_and_rate(request, call_next):
        # No proxy-header trust: source is the actual TCP peer. Firewall is an independent layer.
        try:
            policy = store.policy()
            peer = ipaddress.ip_address(request.client.host)
            networks = [ipaddress.ip_network(n) for n in policy['allowed_cidrs']]
            if not any(peer.version == n.version and peer in n for n in networks):
                return reply(403, 'SOURCE_NOT_ALLOWED')
            if not throttle.allow(str(peer)):
                response = reply(429, 'RATE_LIMITED')
                response.headers['Retry-After'] = '60'
                return response
            request.state.policy = policy
        except Exception:
            return reply(503, 'CONFIGURATION_UNAVAILABLE')
        response = await call_next(request)
        response.headers.update(NO_CACHE)
        return response

    @app.get('/healthz')
    def health():
        return {'ok': True, 'service': 'maas-bootstrap-config'}

    def fetch(request, revision=None):
        try:
            grant = auth.authenticate(request.headers.get('authorization'), request.state.policy['grants'], time.time())
            data = store.release(grant, revision)
            image = request.query_params.get('image_id')
            if not image:
                raise AccessFailure(400, 'IMAGE_ID_REQUIRED')
            if image not in data['manifest']['compatible_image_ids']:
                raise AccessFailure(409, 'IMAGE_INCOMPATIBLE')
            return JSONResponse(data, headers=NO_CACHE)
        except AccessFailure as e:
            return reply(e.status, e.code)
        except Exception:
            # Never echo private paths, config contents, tokens or framework validation payloads.
            return reply(503, 'CONFIGURATION_UNAVAILABLE')

    @app.get('/bootstrap/v1/config')
    def current(request: Request):
        return fetch(request)

    @app.get('/bootstrap/v1/configs/{revision}')
    def exact(revision: str, request: Request):
        return fetch(request, revision)

    return app
