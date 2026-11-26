"""Ask MaaS for exclusive authorization before entering the GPU engine."""
from concurrent.futures import Future
from http.client import IncompleteRead
import hashlib
import hmac
import json
import logging
import os
from pathlib import Path
import re
import socket
import ssl
import stat
import tempfile
import threading
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, HTTPSHandler, ProxyHandler, Request, build_opener
import uuid

from fastapi import HTTPException

ATTEMPT_HEADER = 'X-MaaS-Execution-Attempt'
PATH = '/maas/internal/pp-ocr-execution'


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


class ExecutionGuard:
    def __init__(self, base_url, secret, gpu_uuid, route, ca_file=None, receipt_dir=None):
        base = urlsplit(base_url)
        if (base.scheme != 'https' or not base.hostname or base.username is not None or
                base.path not in ('', '/') or base.query or base.fragment):
            raise ValueError('Execution guard requires a private HTTPS origin')
        if not re.fullmatch(r'GPU-[0-9a-f]{8}(-[0-9a-f]{4}){3}-[0-9a-f]{12}', gpu_uuid):
            raise ValueError('Guarded OCR requires exactly one visible GPU UUID')
        if not re.fullmatch('[a-f0-9]{64}', secret) or not 0 < len(route) <= 128:
            raise ValueError('Invalid worker execution credential or route')
        self._receipt_lock = threading.Lock()
        self.runtime_binding = None
        self.receipt_dir = Path(receipt_dir) if receipt_dir else None
        if self.receipt_dir and (not self.receipt_dir.is_absolute() or not self.receipt_dir.is_dir()
                                 or self.receipt_dir.is_symlink() or self.receipt_dir.stat().st_mode & 0o007):
            raise ValueError('Execution receipts require a private persistent directory')
        self.url = base_url.rstrip('/') + PATH
        self.headers = {'Authorization': 'Bearer ' + secret, 'Content-Type': 'application/json'}
        self.identity = {'gpu_uuid': gpu_uuid, 'instance': uuid.uuid4().hex, 'route': route}
        context = ssl.create_default_context(cafile=ca_file)
        self.open = build_opener(ProxyHandler({}), HTTPSHandler(context=context), NoRedirect()).open

    @classmethod
    def from_env(cls, gpu_uuid, prefix='MAAS_PP_OCR_EXECUTION_'):
        path = Path(os.environ[prefix+'KEY_FILE'])
        if not path.is_file() or stat.S_IMODE(path.stat().st_mode) & 0o077:
            raise ValueError('Worker execution credential must be a private file')
        guard = cls(os.environ[prefix+'ORIGIN'], path.read_text().strip(), gpu_uuid,
                    os.environ[prefix+'ROUTE'], os.getenv(prefix+'CA_FILE'), os.getenv(prefix+'RECEIPT_DIR'))
        if witness := os.getenv(prefix+'RUNTIME_SOCKET'):
            guard.bind_runtime(witness)
        return guard

    def bind_runtime(self, path):
        with socket.socket(socket.AF_UNIX) as local:
            local.settimeout(3)
            local.connect(path)
            local.sendall(self.identity['instance'].encode() + b'\n')
            with local.makefile('rb') as stream:
                binding = stream.readline(2049)
        if len(binding) > 2048 or not binding.endswith(b'\n'):
            raise ValueError('Runtime witness binding unavailable')
        evidence = json.loads(binding)
        if not isinstance(evidence, dict) or set(evidence) != {'identity', 'signature'}:
            raise ValueError('Invalid runtime witness binding')
        self.runtime_binding = evidence

    def proof(self, nonce):
        if not isinstance(nonce, str) or not re.fullmatch('[0-9a-f]{64}', nonce):
            raise HTTPException(400, 'Invalid challenge')
        identity = self.identity
        message = '\n'.join(('maas-guard-v1', nonce, identity['gpu_uuid'], identity['route'], identity['instance']))
        secret = self.headers['Authorization'].removeprefix('Bearer ').encode()
        return {'instance': identity['instance'],
                'signature': hmac.new(secret, message.encode(), hashlib.sha256).hexdigest()}

    def report(self, attempt, *, finished):
        if not re.fullmatch(r'(task|sync)\.[0-9a-f]{32}', attempt):
            raise HTTPException(403, 'A MaaS execution attempt is required')
        event = {**self.identity, 'attempt': attempt, 'finished': finished}
        if not finished and self.runtime_binding:
            event['runtime'] = self.runtime_binding
        request = Request(self.url, method='POST', headers=self.headers, data=json.dumps(event).encode())
        try:
            with self.open(request, timeout=3) as response:
                if response.status == 204:
                    return
        except HTTPError as exc:
            exc.close()
            if exc.code == 409:
                raise HTTPException(409, 'Execution not granted') from None
        # A peer that closes before response headers raises RemoteDisconnected
        # (a ConnectionError) directly, outside urllib's URLError wrapper.
        except (URLError, TimeoutError, ConnectionError, IncompleteRead, ssl.SSLEOFError):
            pass
        raise HTTPException(503, 'Execution authority unavailable')

    def save_finish(self, attempt):
        if not re.fullmatch(r'(task|sync)\.[0-9a-f]{32}', attempt):
            raise HTTPException(403, 'Invalid execution attempt')
        payload = json.dumps({**self.identity, 'attempt': attempt, 'finished': True},
                             sort_keys=True, separators=(',', ':')).encode()
        key = self.headers['Authorization'].removeprefix('Bearer ').encode()
        signature = hmac.new(key, b'maas-gpu-finish-v1\0' + payload, hashlib.sha256).hexdigest()
        path = self.receipt_dir / attempt
        # Readers see a complete, signed record or no record. No inference data is stored.
        fd, pending = tempfile.mkstemp(prefix='.pending-', dir=self.receipt_dir)
        try:
            with os.fdopen(fd, 'wb') as f:
                os.fchmod(f.fileno(), 0o640)
                f.write(signature.encode() + b'\n' + payload)
                f.flush()
                os.fsync(f.fileno())
            os.replace(pending, path)
            directory = os.open(self.receipt_dir, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
        finally:
            Path(pending).unlink(missing_ok=True)
        return path

    def receipt_io(self, operation, *args, **kwargs):
        # A stalled mount must not hold the engine lock or create an unbounded I/O queue.
        if not self._receipt_lock.acquire(timeout=.05):
            raise TimeoutError('Receipt storage busy')
        result = Future()

        def run():
            try:
                result.set_result(operation(*args, **kwargs))
            except Exception as exc:
                result.set_exception(exc)
            finally:
                self._receipt_lock.release()

        try:
            threading.Thread(target=run, name='maas-receipt-io', daemon=True).start()
        except RuntimeError as exc:
            self._receipt_lock.release()
            raise OSError('Receipt writer unavailable') from exc
        return result.result(timeout=.25)

    def report_finished(self, attempt):
        path = None
        if self.receipt_dir:
            try:
                path = self.receipt_io(self.save_finish, attempt)
            except OSError:
                # A storage outage must not suppress a working authenticated callback.
                logging.getLogger(__name__).error('execution_receipt_storage_unavailable')
        try:
            self.report(attempt, finished=True)
        except HTTPException as exc:
            if exc.status_code != 503:
                raise
            self.report(attempt, finished=True)
        if path:
            try:
                self.receipt_io(path.unlink, missing_ok=True)
            except OSError:
                logging.getLogger(__name__).warning('execution_receipt_cleanup_deferred')

    def run(self, attempt, engine, image):
        self.report(attempt, finished=False)
        result = engine(image)
        # ONNX Run returned host output. An engine exception leaves the grant unresolved.
        self.report_finished(attempt)
        return result
