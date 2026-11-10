"""Clone-side access to a scoped, per-boot worker permit."""

from datetime import datetime, timezone
import hashlib
import json
import re
import urllib.error
import urllib.request

from .client import BootstrapError, MAX_BYTES, StaticEndpointResolver, canonical, https_opener, read_file


GPU_UUID = re.compile(r'GPU-[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}', re.IGNORECASE)
BOOT_GRANT = re.compile(r'([0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12})\.[0-9a-f]{64}')
PERMIT = re.compile(r'gpustack_maas-handoff_[0-9a-f]{64}')


def gpu_uuid(value):
    if not isinstance(value, str) or not GPU_UUID.fullmatch(value):
        raise BootstrapError('GPU_UUID_INVALID')
    return 'GPU-' + value[4:].lower()


def positive_id(value):
    return type(value) is int and value > 0


def check_binding(data, cluster_id, gpu, expected_id=None):
    if (not isinstance(data, dict) or not positive_id(data.get('worker_id'))
            or type(data.get('cluster_id')) is not int or data['cluster_id'] != cluster_id
            or data.get('external_id') != gpu or data.get('worker_name') != gpu
            or (expected_id is not None and data['worker_id'] != expected_id)):
        raise BootstrapError('WORKER_BINDING_MISMATCH')


def check_permit(data, cluster_id, gpu, session_id, expected_id=None):
    check_binding(data, cluster_id, gpu, expected_id)
    token = data.get('registration_token')
    if data.get('session_id') != session_id or not isinstance(token, str) or not PERMIT.fullmatch(token):
        raise BootstrapError('WORKER_PERMIT_INVALID')
    try:
        expires = datetime.fromisoformat(data['expires_at'])
        if expires.utcoffset() is None:
            raise ValueError()
    except (KeyError, TypeError, ValueError):
        raise BootstrapError('WORKER_PERMIT_INVALID') from None
    if expires <= datetime.now(timezone.utc):
        raise BootstrapError('WORKER_PERMIT_EXPIRED')
    return token


class WorkerStartFetcher:
    def fetch(self, settings, manifest, gpu):
        try:
            grant = read_file(settings.data['worker_start_grant_file'], secret=True).decode().strip()
        except (KeyError, UnicodeError):
            raise BootstrapError('WORKER_START_GRANT_INVALID') from None
        match = BOOT_GRANT.fullmatch(grant)
        if not match:
            raise BootstrapError('WORKER_START_GRANT_INVALID')
        body = {'cluster_id':manifest['cluster_id'], 'gpu_uuid':gpu, 'image_id':settings.data['image_id'],
                'config_revision':manifest['config_revision']}
        url = StaticEndpointResolver().resolve(settings) + '/bootstrap/v1/worker-start'
        request = urllib.request.Request(url, data=canonical(body), headers={
            'Authorization':'Bearer ' + grant, 'Content-Type':'application/json'}, method='POST')
        try:
            with https_opener(settings).open(request, timeout=25) as response:
                raw = response.read(MAX_BYTES + 1)
                if response.status != 200 or len(raw) > MAX_BYTES:
                    raise BootstrapError('WORKER_PERMIT_INVALID')
                try:result = json.loads(raw)
                except (ValueError, UnicodeError):raise BootstrapError('WORKER_PERMIT_INVALID') from None
        except urllib.error.HTTPError as error:
            code = {401:'WORKER_START_DENIED', 403:'WORKER_START_DENIED', 409:'WORKER_START_BLOCKED'}
            raise BootstrapError(code.get(error.code, 'WORKER_START_UNAVAILABLE')) from None
        except (OSError, TimeoutError):
            raise BootstrapError('WORKER_START_UNAVAILABLE') from None
        check_permit(result, manifest['cluster_id'], gpu, match[1])
        return result


def permit_fingerprint(token):
    return hashlib.sha256(token.encode()).hexdigest()
