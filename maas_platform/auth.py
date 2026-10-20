"""Allowlisted GPUStack credentials; no client-supplied identity headers or auth cache."""
from dataclasses import dataclass
import hashlib, hmac, json
import httpx
from .config import private_text
from .errors import ServiceError


@dataclass(frozen=True)
class Identity:
    credential_id: str
    project: str
    models: tuple


class AuthConnector:
    def __init__(self, settings, client=None):
        self.base = settings.gpustack_url
        self.client = client or httpx.Client(timeout=10, follow_redirects=False, trust_env=False)
        self.pepper = private_text(settings.pepper_file).encode()
        self.bindings_file = settings.bindings_file

    def bindings(self):
        # Reload on every validation: local disable/revoke takes effect without API restart.
        try:
            data = json.loads(private_text(self.bindings_file))
            rows = [r for r in data['credentials'] if r.get('enabled', True)]
            ids, fingerprints = set(), set()
            for row in rows:
                if (not isinstance(row['id'], str) or not row['id']
                        or not isinstance(row['project'], str) or not row['project']
                        or not isinstance(row['models'], list) or not row['models']
                        or any(not isinstance(m, str) or not m for m in row['models'])):
                    raise ValueError('Invalid credential binding')
                digest = self._fingerprint(private_text(row['secret_file']))
                if row['id'] in ids or digest in fingerprints:
                    raise ValueError('Ambiguous credential binding')
                ids.add(row['id']); fingerprints.add(digest)
            return rows
        except (OSError, ValueError, KeyError, TypeError):
            raise ServiceError('BINDING_INVALID', 'Credential registry unavailable or ambiguous', 503)


    def _fingerprint(self, value):
        return hmac.new(self.pepper, value.encode(), hashlib.sha256).digest()

    def _request(self, path, key, model=None):
        headers = {'Authorization': 'Bearer ' + key}
        if model:
            headers['x-higress-llm-model'] = model
        try:
            # Build a fresh request instead of merging the shared client's cookie jar.
            # Credential A must never authenticate through an ambient session from B.
            response = self.client.send(httpx.Request('GET', self.base + path, headers=headers))
        except httpx.HTTPError:
            raise ServiceError('AUTH_UNAVAILABLE', 'Upstream authentication unavailable', 503, True)
        if response.status_code in (401, 403, 404):
            raise ServiceError('AUTH_DENIED', 'Credential or model access denied', 403)
        if response.status_code != 200:
            raise ServiceError('AUTH_UNAVAILABLE', 'Upstream authentication unavailable', 503, True)

    def authenticate(self, key):
        if not key or len(key) > 1024:
            raise ServiceError('UNAUTHENTICATED', 'Supply a registered inference key', 401)
        for row in self.bindings():
            expected = private_text(row['secret_file'])
            if hmac.compare_digest(self._fingerprint(key), self._fingerprint(expected)):
                self._request('/v1/models', key)
                return Identity(row['id'], row['project'], tuple(row['models']))
        raise ServiceError('UNAUTHENTICATED', 'Supply a registered inference key', 401)

    def key_for(self, identity):
        for row in self.bindings():
            if row['id'] == identity.credential_id and row['project'] == identity.project:
                return private_text(row['secret_file'])
        raise ServiceError('AUTH_REVOKED', 'Credential binding revoked', 403)

    def authorize(self, identity, name, model):
        if name not in identity.models:
            raise ServiceError('MODEL_FORBIDDEN', 'Model not allowed for this credential', 403)
        key = self.key_for(identity)
        # A local mock capability is explicitly granted by the binding, not a GPUStack model.
        if model['driver'] != 'mock':
            self._request('/token-auth', key, model['upstream_model'])

    def for_task(self, credential_id, project):
        for row in self.bindings():
            if row['id'] == credential_id and row['project'] == project:
                identity = self.authenticate(private_text(row['secret_file']))
                if identity.project == project:
                    return identity
        raise ServiceError('AUTH_REVOKED', 'Credential binding revoked', 403)
