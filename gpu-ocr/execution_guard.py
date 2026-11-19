"""Ask MaaS for exclusive authorization before entering the GPU engine."""
import json
import os
from pathlib import Path
import re
import ssl
import stat
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
    def __init__(self, base_url, secret, gpu_uuid, route, ca_file=None):
        base = urlsplit(base_url)
        if (base.scheme != 'https' or not base.hostname or base.username is not None or
                base.path not in ('', '/') or base.query or base.fragment):
            raise ValueError('Execution guard requires a private HTTPS origin')
        if not re.fullmatch(r'GPU-[0-9a-f]{8}(-[0-9a-f]{4}){3}-[0-9a-f]{12}', gpu_uuid):
            raise ValueError('Guarded OCR requires exactly one visible GPU UUID')
        if not re.fullmatch('[a-f0-9]{64}', secret) or not 0 < len(route) <= 128:
            raise ValueError('Invalid worker execution credential or route')
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
        return cls(os.environ[prefix+'ORIGIN'], path.read_text().strip(), gpu_uuid,
                   os.environ[prefix+'ROUTE'], os.getenv(prefix+'CA_FILE'))

    def report(self, attempt, *, finished):
        if not re.fullmatch(r'(task|sync)\.[0-9a-f]{32}', attempt):
            raise HTTPException(403, 'A MaaS execution attempt is required')
        request = Request(self.url, method='POST', headers=self.headers,
                          data=json.dumps({**self.identity, 'attempt': attempt, 'finished': finished}).encode())
        try:
            with self.open(request, timeout=3) as response:
                if response.status == 204:
                    return
        except HTTPError as exc:
            exc.close()
            if exc.code == 409:
                raise HTTPException(409, 'Execution not granted') from None
        except (URLError, TimeoutError):
            pass
        raise HTTPException(503, 'Execution authority unavailable')

    def report_finished(self, attempt):
        try:
            self.report(attempt, finished=True)
        except HTTPException as exc:
            if exc.status_code != 503:
                raise
            self.report(attempt, finished=True)

    def run(self, attempt, engine, image):
        self.report(attempt, finished=False)
        result = engine(image)
        # ONNX Run returned host output. An engine exception leaves the grant unresolved.
        self.report_finished(attempt)
        return result
