"""MaaS worker-start broker and operator approval command."""

import argparse
import hashlib
import hmac
import json
import logging
import os
from pathlib import Path
from secrets import token_hex
import stat
import time
import urllib.error
import urllib.parse
import urllib.request
from uuid import UUID, uuid4

from .client import BootstrapError, DISKLESS_POLICY, LocalState, MAX_BYTES, NoRedirect, SAFE_REVISION
from .client import atomic_bytes, atomic_json, canonical, read_file
from .execution import KEY, CredentialStore, configuration, witness_routes
from .handoff import BOOT_GRANT, check_binding, check_permit, gpu_uuid, positive_id


class StartDenied(Exception):
    def __init__(self, status, code):
        self.status, self.code = status, code
        super().__init__(code)


class NativeEnrollment:
    def __init__(self, base, key_file):
        self.base = base.rstrip('/')
        url = urllib.parse.urlsplit(self.base)
        if (not url.hostname or url.username or url.password or url.path or url.query or url.fragment
                or (url.scheme != 'https' and not (url.scheme == 'http' and url.hostname in ('127.0.0.1','::1')))):
            raise BootstrapError('ENROLLMENT_ENDPOINT_INVALID')
        self.key_file = key_file

    def post(self, action, body):
        key = read_file(self.key_file, secret=True).decode().strip()
        if len(key) < 32 or any(ord(ch) < 33 or ord(ch) > 126 for ch in key):
            raise BootstrapError('ENROLLMENT_CREDENTIAL_INVALID')
        request = urllib.request.Request(self.base + '/internal/maas/worker-' + action, data=canonical(body),
            headers={'Authorization':'Bearer ' + key, 'Content-Type':'application/json'}, method='POST')
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
        try:
            with opener.open(request, timeout=8) as response:
                raw = response.read(MAX_BYTES + 1)
                if response.status != 200 or len(raw) > MAX_BYTES:
                    raise BootstrapError('ENROLLMENT_RESPONSE_INVALID')
                return json.loads(raw)
        except urllib.error.HTTPError as error:
            if error.code in (401,403,404,409,422):
                raise StartDenied(409, 'NATIVE_' + action.upper() + '_REJECTED') from None
            raise BootstrapError('ENROLLMENT_UNAVAILABLE') from None
        except (OSError, ValueError):
            raise BootstrapError('ENROLLMENT_UNAVAILABLE') from None


def private_root(path):
    root = Path(path)
    st = root.lstat()
    if not stat.S_ISDIR(st.st_mode) or st.st_mode & 0o077 or st.st_uid not in (0, os.geteuid()):
        raise BootstrapError('WORKER_START_DIRECTORY_UNTRUSTED')
    return root


class WorkerStartBroker:
    def __init__(self, root, native, shutdown_key_file=None, execution_keys_dir=None, witness_keys_dir=None):
        self.root = private_root(root)
        self.native = native
        self.shutdown_key_file = shutdown_key_file or os.getenv('MAAS_BOOTSTRAP_SHUTDOWN_EVENT_KEY_FILE')
        self.execution_keys = CredentialStore(execution_keys_dir) if execution_keys_dir else None
        if witness_keys_dir is None and execution_keys_dir:
            provisioned = Path(execution_keys_dir) / 'host-witness'
            if provisioned.exists():
                witness_keys_dir = provisioned
        self.witness_keys = LocalState(private_root(witness_keys_dir)) if witness_keys_dir else None

    def approved_permit(self, state, grant, session_id, manifest, boot, witness_key=None):
        runtime = configuration(manifest)
        needs_key = runtime is not None and 'execution' in runtime
        if needs_key and self.execution_keys is None:
            raise BootstrapError('EXECUTION_CREDENTIAL_STORE_UNAVAILABLE')
        managed = bool(runtime and witness_routes(runtime))
        if managed:
            if self.witness_keys is None:
                raise BootstrapError('RUNTIME_WITNESS_STORE_UNAVAILABLE')
            if not boot or not isinstance(witness_key, str) or not KEY.fullmatch(witness_key):
                raise StartDenied(400, 'RUNTIME_WITNESS_KEY_REQUIRED')
            # Pin the client's commitment before obtaining the native permit. Replaying
            # an approved startup must never replace the signer or reveal it in a reply.
            binding = {'boot': boot, 'commitment': hashlib.sha256(witness_key.encode()).hexdigest()}
            existing = state.read('witness-binding.json')
            if existing is None and state.read('issue.json') is None:
                atomic_json(state.root/'witness-binding.json', binding)
            elif existing != binding:
                raise StartDenied(409, 'RUNTIME_WITNESS_BINDING_CONFLICT')
        elif witness_key is not None:
            raise StartDenied(400, 'UNEXPECTED_RUNTIME_WITNESS_KEY')
        permit = self.issue(state, grant, session_id)
        if needs_key:
            permit = {**permit, 'execution_key': self.execution_keys.issue(permit['external_id'])}
        if managed:
            if hmac.compare_digest(witness_key, permit['execution_key']):
                raise StartDenied(409, 'RUNTIME_WITNESS_BINDING_CONFLICT')
            path = self.witness_keys.root / (gpu_uuid(permit['external_id']) + '.' + boot + '.key')
            content = (witness_key + '\n').encode()
            with self.witness_keys.lock():
                if path.exists() or path.is_symlink():
                    if not hmac.compare_digest(read_file(path, secret=True), content):
                        raise StartDenied(409, 'RUNTIME_WITNESS_BINDING_CONFLICT')
                else:
                    atomic_bytes(path, content)
            permit = {**permit, 'witness_enrolled': True}
        return self.shutdown_credential(permit, boot)

    def shutdown_credential(self, permit, boot_id):
        if not self.shutdown_key_file or boot_id is None:
            return permit
        try:
            from .shutdown_auth import issue
            scope = {k: permit[k] for k in ('cluster_id', 'worker_id', 'session_id')}
            scope.update(gpu_uuid=permit['external_id'], boot_id=boot_id)
            return {**permit, 'shutdown_event_credential': issue(self.shutdown_key_file, scope)}
        except Exception:
            # Optional reporting cannot turn a valid native permit into a startup failure.
            logging.getLogger(__name__).warning('shutdown_event_credential_unavailable')
            return permit

    def automatic(self, grant, body, releases):
        if not isinstance(body, dict) or set(body) - {'witness_key'} != {'cluster_id','gpu_uuid','image_id','config_revision','boot_id'}:
            raise StartDenied(400, 'WORKER_START_REQUEST_INVALID')
        try:
            gpu = gpu_uuid(body['gpu_uuid'])
            boot = str(UUID(body['boot_id']))
        except (BootstrapError, ValueError, TypeError, AttributeError):
            raise StartDenied(400, 'WORKER_START_REQUEST_INVALID') from None
        cluster = body['cluster_id']
        if not positive_id(cluster) or not isinstance(body['config_revision'], str):
            raise StartDenied(400, 'WORKER_START_REQUEST_INVALID')
        manifest = releases.release(grant, body['config_revision'], body['image_id'])['manifest']
        if (manifest.get('worker_name_policy') != DISKLESS_POLICY or manifest['cluster_id'] != cluster
                or body['image_id'] not in manifest['compatible_image_ids']):
            raise StartDenied(403, 'WORKER_START_SCOPE_MISMATCH')
        approved = grant.get('auto_start_gpus', {})
        auto_enroll = grant.get('auto_enroll_gpus', False)
        if not isinstance(approved, dict) or type(auto_enroll) is not bool:
            raise BootstrapError('WORKER_START_POLICY_INVALID')
        worker_id = approved.get(gpu)
        if gpu in approved and not positive_id(worker_id):
            raise BootstrapError('WORKER_START_POLICY_INVALID')
        if worker_id is None and not auto_enroll:
            raise StartDenied(403, 'WORKER_START_GPU_NOT_APPROVED')
        # issue() enrolls an unknown GPU under these locks before requesting a permit.
        gpu_state = LocalState(self.root / f'auto-{cluster}-{gpu}')
        state = LocalState(gpu_state.root / boot)
        binding = dict({k:v for k,v in body.items() if k != 'witness_key'}, boot_id=boot, gpu_uuid=gpu, worker_id=worker_id,
                       site_id=manifest['site_id'], batch_id=manifest['batch_id'], automatic=True)
        with gpu_state.lock(), state.lock():
            intent = state.read('intent.json')
            if intent is None:
                intent = {'binding':binding, 'session_id':str(uuid4())}
                atomic_json(state.root/'intent.json', intent)
            elif intent['binding'] != binding:
                raise StartDenied(409, 'WORKER_START_BOOT_CONFLICT')
            return self.approved_permit(state, binding, intent['session_id'], manifest, boot, body.get('witness_key'))

    def start(self, authorization, body, releases):
        token = (authorization or '').removeprefix('Bearer ')
        match = BOOT_GRANT.fullmatch(token)
        if not (authorization or '').startswith('Bearer ') or match is None:
            raise StartDenied(401, 'WORKER_START_GRANT_INVALID')
        session_id = match[1]
        state = LocalState(self.root / session_id)
        if state.root.is_symlink() or not state.root.is_dir():
            raise StartDenied(401, 'WORKER_START_GRANT_INVALID')
        with state.lock():
            grant = state.read('grant.json')
            if not grant or not hmac.compare_digest(grant['token_sha256'], hashlib.sha256(token.encode()).hexdigest()):
                raise StartDenied(401, 'WORKER_START_GRANT_INVALID')
            if grant['expires_at'] <= time.time():
                raise StartDenied(403, 'WORKER_START_GRANT_EXPIRED')
            expected = {key:grant[key] for key in ('cluster_id','gpu_uuid','image_id','config_revision')}
            if (not isinstance(body, dict) or not positive_id(body.get('cluster_id'))
                    or {k:v for k,v in body.items() if k not in ('boot_id','witness_key')} != expected):
                raise StartDenied(403, 'WORKER_START_SCOPE_MISMATCH')
            boot = body.get('boot_id')
            if 'boot_id' in body:
                try: boot = str(UUID(boot))
                except (ValueError, TypeError, AttributeError):
                    raise StartDenied(400, 'WORKER_START_REQUEST_INVALID') from None
            manifest = releases.release(grant, grant['config_revision'], grant['image_id'])['manifest']
            if (manifest.get('worker_name_policy') != DISKLESS_POLICY
                    or grant['image_id'] not in manifest['compatible_image_ids']):
                raise StartDenied(409, 'WORKER_START_RELEASE_MISMATCH')
            return self.approved_permit(state, grant, session_id, manifest, boot, body.get('witness_key'))

    def issue(self, state, grant, session_id):
        cluster, gpu = grant['cluster_id'], grant['gpu_uuid']
        saved = state.read('issue.json') or {}
        if saved.get('phase') == 'issued':
            check_permit(saved['permit'], cluster, gpu, session_id, grant.get('worker_id'))
            return saved['permit']
        if saved.get('phase') not in (None, 'retryable'):
            raise StartDenied(409, 'WORKER_START_NEEDS_RECONCILIATION')
        worker_id = grant.get('worker_id') or saved.get('worker_id')
        if worker_id is None:
            result = self.native.post('enrollment', {'cluster_id':cluster, 'gpu_uuid':gpu})
            check_binding(result, cluster, gpu)
            worker_id = result['worker_id']
        body = {'worker_id':worker_id, 'gpu_uuid':gpu, 'session_id':session_id}
        if grant.get('automatic'):
            body['automatic'] = True
        if grant.get('previous_session_id'):
            body.update(previous_session_id=grant['previous_session_id'], fence_receipt=grant['fence_receipt'])
        # The plugin returns the permit only once. Persist intent before any
        # ambiguous request so a lost response cannot trigger another issuance.
        atomic_json(state.root / 'issue.json', {'phase':'requesting', 'worker_id':worker_id})
        try:
            permit = self.native.post('handoff', body)
        except StartDenied:
            atomic_json(state.root / 'issue.json', {'phase':'retryable', 'worker_id':worker_id})
            raise
        check_permit(permit, cluster, gpu, session_id, worker_id)
        atomic_json(state.root / 'issue.json', {'phase':'issued', 'permit':permit})
        return permit


def approve(root, output, *, site_id, batch_id, cluster_id, gpu, image_id, config_revision,
            lifetime=600, worker_id=None, previous_session_id=None, fence_receipt=None):
    root = private_root(root)
    values = (site_id, batch_id, image_id, config_revision)
    if not all(isinstance(value, str) and SAFE_REVISION.fullmatch(value) for value in values):
        raise BootstrapError('WORKER_START_SCOPE_INVALID')
    if not positive_id(cluster_id) or not positive_id(lifetime) or lifetime > 1800:
        raise BootstrapError('WORKER_START_SCOPE_INVALID')
    gpu = gpu_uuid(gpu)
    if any(value is not None for value in (worker_id, previous_session_id, fence_receipt)):
        try:
            if not positive_id(worker_id) or not isinstance(fence_receipt, str) or not 1 <= len(fence_receipt.strip()) <= 128:
                raise ValueError()
            previous_session_id = str(UUID(previous_session_id))
        except (ValueError, TypeError, AttributeError):
            raise BootstrapError('WORKER_START_FENCE_INVALID') from None
    session_id = str(uuid4())
    token = session_id + '.' + token_hex(32)
    record = {'site_id':site_id, 'batch_id':batch_id, 'cluster_id':cluster_id, 'gpu_uuid':gpu,
              'image_id':image_id, 'config_revision':config_revision, 'expires_at':time.time() + lifetime,
              'token_sha256':hashlib.sha256(token.encode()).hexdigest(), 'worker_id':worker_id,
              'previous_session_id':previous_session_id, 'fence_receipt':fence_receipt}
    fd = os.open(output, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'w') as stream:
        stream.write(token + '\n');stream.flush();os.fsync(stream.fileno())
    state = LocalState(root / session_id)
    with state.lock():
        atomic_json(state.root / 'grant.json', record)
    return {'code':'WORKER_START_APPROVED', 'session_id':session_id, 'gpu_uuid':gpu, 'cluster_id':cluster_id}


def main():
    parser = argparse.ArgumentParser(description='Issue a private grant after operator verification')
    for option in ('root','output','site-id','batch-id','gpu','image-id','config-revision'):
        parser.add_argument('--' + option, required=True)
    parser.add_argument('--cluster-id', type=int, required=True)
    parser.add_argument('--lifetime', type=int, default=600)
    parser.add_argument('--worker-id', type=int)
    parser.add_argument('--previous-session-id')
    parser.add_argument('--fence-receipt')
    try:
        result = approve(**vars(parser.parse_args()))
    except BootstrapError as error:
        print(json.dumps({'code':error.code}));return 1
    except Exception:
        print(json.dumps({'code':'WORKER_START_APPROVAL_FAILED'}));return 1
    print(json.dumps(result));return 0


if __name__ == '__main__':
    raise SystemExit(main())
