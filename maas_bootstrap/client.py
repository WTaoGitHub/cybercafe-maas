"""Small clone-side verified configuration client; no remote VM/session registration."""
import base64
import contextlib
import fcntl
import hashlib
import json
import os
import platform
import random
import re
import ssl
import stat
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

MAX_BYTES = 1024 * 1024
SAFE_REVISION = re.compile(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,79}\Z')
IMAGE_DIGEST = re.compile(r'[a-z0-9][a-z0-9./:_-]*@sha256:[0-9a-f]{64}\Z')


class BootstrapError(Exception):
    def __init__(self, code):
        self.code = code
        super().__init__(code)


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False).encode()


def read_file(path, secret=False):
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
        with os.fdopen(fd, 'rb') as f:
            s = os.fstat(f.fileno())
            if not stat.S_ISREG(s.st_mode) or s.st_uid not in (0, os.geteuid()):
                raise BootstrapError('LOCAL_FILE_UNTRUSTED')
            if s.st_mode & (0o077 if secret else 0o022):
                raise BootstrapError('LOCAL_FILE_PERMISSIONS')
            raw = f.read(MAX_BYTES + 1)
            if len(raw) > MAX_BYTES:
                raise BootstrapError('LOCAL_FILE_TOO_LARGE')
            return raw
    except OSError:
        raise BootstrapError('LOCAL_FILE_UNAVAILABLE') from None


def atomic_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix='.' + path.name, dir=path.parent)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, 'wb') as f:
            f.write(canonical(value) + b'\n');f.flush();os.fsync(f.fileno())
        os.replace(name, path)
        directory = os.open(path.parent, os.O_RDONLY)
        try:os.fsync(directory)
        finally:os.close(directory)
    finally:
        if os.path.exists(name):os.unlink(name)


class LocalSettings:
    def __init__(self, path):
        try:
            self.data = json.loads(read_file(path, secret=True))
            d = self.data
            if d['schema_version'] != 1:
                raise ValueError()
            self.base = d['config_service_url'].rstrip('/')
            u = urllib.parse.urlsplit(self.base)
            if u.scheme != 'https' or not u.hostname or u.username or u.password or u.query or u.fragment or u.path:
                raise ValueError()
            for k in ('shared_code_file','tls_certificate_file','release_public_key_file','state_dir','worker_data_dir'):
                if not Path(d[k]).is_absolute():raise ValueError()
            from .storage import paths
            d['model_root'],d['model_cache_dir']=map(str,paths(d))
            allowed=d.get('allowed_registry_endpoints',[])
            from .registry import ENDPOINT
            if not isinstance(allowed,list) or any(not isinstance(v,str) or not ENDPOINT.fullmatch(v) or int(v.rsplit(':',1)[1])>65535 for v in allowed):raise ValueError()
            if not isinstance(d['cluster_id'], int) or isinstance(d['cluster_id'], bool):raise ValueError()
            if not d['allowed_server_urls'] or not isinstance(d['allowed_server_urls'], list):raise ValueError()
            if not SAFE_REVISION.fullmatch(d['image_id']):raise ValueError()
        except BootstrapError:raise
        except Exception:raise BootstrapError('LOCAL_CONFIG_INVALID') from None


class StaticEndpointResolver:
    def resolve(self, settings):
        # cloud_backup is a reserved field. It is never contacted, even when primary fails.
        if settings.data.get('cloud_backup', {}).get('enabled', False):
            raise BootstrapError('CLOUD_BACKUP_UNSUPPORTED')
        return settings.base


class SharedCodeFileProvider:
    def get(self, settings):
        code = read_file(settings.data['shared_code_file'], secret=True).decode().strip()
        if not 24 <= len(code) <= 512 or any(ord(x) < 33 or ord(x) > 126 for x in code):
            raise BootstrapError('LOCAL_CREDENTIAL_INVALID')
        return code


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class HTTPSConfigFetcher:
    def __init__(self, endpoint=None, credential=None, sleeper=time.sleep):
        self.endpoint = endpoint or StaticEndpointResolver()
        self.credential = credential or SharedCodeFileProvider()
        self.sleep = sleeper

    def fetch(self, settings):
        base = self.endpoint.resolve(settings)
        cert = read_file(settings.data['tls_certificate_file']).decode()
        try:ctx = ssl.create_default_context(cadata=cert)
        except (ssl.SSLError, ValueError):raise BootstrapError('TLS_TRUST_INVALID') from None
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect(), urllib.request.HTTPSHandler(context=ctx))
        url = base + '/bootstrap/v1/config?' + urllib.parse.urlencode({'image_id':settings.data['image_id']})
        headers = {'Authorization':'Bearer ' + self.credential.get(settings)}
        failure = 'CONFIG_SERVICE_UNAVAILABLE'
        for attempt in range(3):
            try:
                with opener.open(urllib.request.Request(url, headers=headers), timeout=8) as r:
                    raw = r.read(MAX_BYTES + 1)
                    if len(raw) > MAX_BYTES:raise BootstrapError('CONFIG_RESPONSE_TOO_LARGE')
                    if r.status != 200:raise BootstrapError('CONFIG_RESPONSE_INVALID')
                    try:return json.loads(raw)
                    except (ValueError, UnicodeError):raise BootstrapError('CONFIG_RESPONSE_INVALID') from None
            except urllib.error.HTTPError as e:
                if e.code in (401,403):raise BootstrapError('CONFIG_AUTH_DENIED') from None
                if e.code == 409:raise BootstrapError('CONFIG_INCOMPATIBLE') from None
                if e.code not in (429,502,503,504):raise BootstrapError('CONFIG_HTTP_REJECTED') from None
                failure = 'CONFIG_RATE_LIMITED' if e.code == 429 else 'CONFIG_SERVICE_UNAVAILABLE'
            except urllib.error.URLError as e:
                if isinstance(e.reason, ssl.SSLCertVerificationError):raise BootstrapError('TLS_VALIDATION_FAILED') from None
            except (OSError, TimeoutError):pass
            if attempt < 2:self.sleep(min(2 ** attempt, 4) + random.uniform(0, .5))
        raise BootstrapError(failure)


class ReleaseVerifier:
    def verify(self, settings, envelope):
        try:
            from cryptography.hazmat.primitives import serialization
            from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
        except ImportError:raise BootstrapError('CRYPTOGRAPHY_UNAVAILABLE') from None
        try:
            d = settings.data;m = envelope['manifest'];sig = envelope['manifest_signature'];registration = envelope['registration']
            pub = serialization.load_pem_public_key(read_file(d['release_public_key_file']))
            if not isinstance(pub, Ed25519PublicKey) or sig['algorithm'] != 'Ed25519' or sig['key_id'] != d['release_key_id']:
                raise BootstrapError('RELEASE_SIGNATURE_INVALID')
            pub.verify(base64.b64decode(sig['value'], validate=True), canonical(m))
            if m['schema_version'] != 1 or not SAFE_REVISION.fullmatch(m['config_revision']):raise ValueError()
            if (m['site_id'],m['batch_id'],m['cluster_id']) != (d['site_id'],d['batch_id'],d['cluster_id']):
                raise BootstrapError('RELEASE_SCOPE_MISMATCH')
            if registration['cluster_id'] != m['cluster_id']:raise BootstrapError('RELEASE_SCOPE_MISMATCH')
            if d['image_id'] not in m['compatible_image_ids']:raise BootstrapError('CONFIG_INCOMPATIBLE')
            if m['architecture'] != d['architecture'] or m['gpustack_version'] != 'v2.2.3':
                raise BootstrapError('CONFIG_INCOMPATIBLE')
            if m['server_url'] not in d['allowed_server_urls'] or m['worker_data_dir'] != d['worker_data_dir']:
                raise BootstrapError('RELEASE_TARGET_NOT_ALLOWED')
            u = urllib.parse.urlsplit(m['server_url'])
            if u.scheme not in ('http','https') or not u.hostname or u.username or u.password or u.path or u.query or u.fragment:
                raise BootstrapError('RELEASE_TARGET_NOT_ALLOWED')
            if not IMAGE_DIGEST.fullmatch(m['worker_image_digest']):raise ValueError()
            if m['model_mounts'] or m['worker_name_policy'] != 'native-independent':
                raise BootstrapError('RELEASE_OPTIONS_UNSUPPORTED')
            if m.get('cloud_backup',{}).get('enabled',False):raise BootstrapError('CLOUD_BACKUP_UNSUPPORTED')
            token = registration['token']
            if not isinstance(token,str) or not 16 <= len(token) <= 4096 or any(ord(x)<33 or ord(x)>126 for x in token):
                raise ValueError()
            from .registry import validate as validate_registry
            validate_registry(settings,envelope)
            # Registration material travels over verified TLS; only the public manifest is signed.
            digest = hashlib.sha256(canonical({'manifest':m,'token_sha256':hashlib.sha256(token.encode()).hexdigest()})).hexdigest()
            return {'revision':m['config_revision'],'fingerprint':digest,'cluster_id':m['cluster_id']}
        except BootstrapError:raise
        except Exception:raise BootstrapError('RELEASE_VERIFICATION_FAILED') from None


class LocalState:
    def __init__(self, root):self.root = Path(root)

    @contextlib.contextmanager
    def lock(self):
        self.root.mkdir(mode=0o700, parents=True, exist_ok=True)
        s = self.root.lstat()
        if not stat.S_ISDIR(s.st_mode) or s.st_mode & 0o077 or s.st_uid not in (0,os.geteuid()):
            raise BootstrapError('STATE_DIRECTORY_UNTRUSTED')
        fd = os.open(self.root/'apply.lock', os.O_CREAT|os.O_RDWR|os.O_NOFOLLOW, 0o600)
        try:
            try:fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
            except BlockingIOError:raise BootstrapError('BOOTSTRAP_BUSY') from None
            yield
        finally:os.close(fd)

    def read(self, name):
        p = self.root/name
        if not p.exists():return None
        try:return json.loads(read_file(p,secret=True))
        except BootstrapError:raise
        except Exception:raise BootstrapError('LOCAL_STATE_INVALID') from None

    def prepare(self, envelope, metadata):
        target = self.root/'generations'/metadata['fingerprint']/'config.json'
        if target.exists():
            try:
                if json.loads(read_file(target,secret=True)) != envelope:raise BootstrapError('LOCAL_GENERATION_CONFLICT')
            except ValueError:raise BootstrapError('LOCAL_STATE_INVALID') from None
        else:atomic_json(target,envelope)
        atomic_json(self.root/'prepared.json',metadata)
        return target

    def status(self):
        return {'prepared':self.read('prepared.json'),'applied':self.read('applied.json'),'last_result':self.read('last-result.json')}


def prepare(settings, fetcher=None, verifier=None):
    state = LocalState(settings.data['state_dir'])
    with state.lock():
        envelope = (fetcher or HTTPSConfigFetcher()).fetch(settings)
        metadata = (verifier or ReleaseVerifier()).verify(settings,envelope)
        state.prepare(envelope,metadata)
        result = {'code':'PREPARED',**metadata}
        atomic_json(state.root/'last-result.json',result)
        return result
