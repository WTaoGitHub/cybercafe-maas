"""Bind an OvisOCR3 attempt to a single vLLM EngineCore request."""
import asyncio
from contextvars import ContextVar
import logging
import re
import subprocess
import sys

from fastapi import HTTPException, Request
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse

from execution_guard import ATTEMPT_HEADER, ExecutionGuard


LOG = logging.getLogger(__name__)
ENV_PREFIX = 'MAAS_OVIS_EXECUTION_'
KEEPALIVE = set()
ATTEMPT = ContextVar('ovis_execution_attempt', default=None)


class Attempt:
    def __init__(self, guard, token):
        self.guard = guard
        self.token = token
        self.submission_started = False
        self.receipt = asyncio.get_running_loop().create_future()
        self.reporting = False

    def finish(self):
        if self.reporting:
            return
        self.reporting = True
        task = asyncio.create_task(self._report())
        KEEPALIVE.add(task)
        task.add_done_callback(KEEPALIVE.discard)

    async def _report(self):
        try:
            await asyncio.to_thread(self.guard.report_finished, self.token)
        except Exception:
            LOG.warning('Ovis execution finish callback unavailable')
            self.receipt.set_result(False)
        else:
            self.receipt.set_result(True)


def install_engine_hooks(client_type):
    if hasattr(client_type, 'process_engine_outputs'):
        raise RuntimeError('Unexpected vLLM EngineCore output hook')
    add_request = client_type.add_request_async
    pending = {}

    async def tracked_add(client, request):
        attempt = ATTEMPT.get()
        key = (client, request.request_id)
        if attempt is None or attempt.submission_started or attempt.reporting or key in pending:
            raise RuntimeError('EngineCore submission requires an unused MaaS authorization')
        attempt.submission_started = True
        pending[key] = attempt
        await add_request(client, request)

    async def observe_core_outputs(client, outputs):
        # This hook sees scheduler-terminal IDs even when the client drops an output envelope with no tokens.
        for request_id in outputs.finished_requests or ():
            attempt = pending.pop((client, request_id), None)
            if attempt is not None:
                attempt.finish()

    client_type.add_request_async = tracked_add
    client_type.process_engine_outputs = observe_core_outputs


class OvisExecutionMiddleware(BaseHTTPMiddleware):
    def __init__(self, app):
        super().__init__(app)
        gpu = subprocess.check_output(['nvidia-smi', '--query-gpu=uuid', '--format=csv,noheader'], text=True).strip()
        self.guard = ExecutionGuard.from_env(gpu, ENV_PREFIX)

    async def dispatch(self, request: Request, call_next):
        if request.url.path in ('/health', '/v1/models', '/metrics', '/version') and request.method == 'GET':
            return await call_next(request)
        if request.url.path != '/v1/chat/completions' or request.method != 'POST':
            return JSONResponse({'detail': 'Endpoint unavailable on guarded OCR service'}, status_code=404)
        tokens = request.headers.getlist(ATTEMPT_HEADER)
        if len(tokens) != 1 or not re.fullmatch(r'(task|sync)\.[0-9a-f]{32}', tokens[0]):
            return JSONResponse({'detail': 'A MaaS execution attempt is required'}, status_code=403)
        try:
            body = await request.json()
        except ValueError:
            return JSONResponse({'detail': 'Invalid chat request'}, status_code=400)
        if not isinstance(body, dict) or body.get('stream', False) is not False or body.get('n', 1) != 1:
            return JSONResponse({'detail': 'Guarded OCR requires one non-streaming completion'}, status_code=422)
        attempt = Attempt(self.guard, tokens[0])
        try:
            await asyncio.to_thread(self.guard.report, attempt.token, finished=False)
        except HTTPException as exc:
            return JSONResponse({'detail': exc.detail}, status_code=exc.status_code)
        marker = ATTEMPT.set(attempt)
        try:
            response = await call_next(request)
            if not attempt.submission_started:
                attempt.finish()
            if response.status_code == 200 or not attempt.submission_started:
                try:
                    if await asyncio.wait_for(asyncio.shield(attempt.receipt), timeout=8):
                        return response
                except TimeoutError:
                    pass
                return JSONResponse({'detail': 'GPU completion is unconfirmed'}, status_code=503)
            return response
        finally:
            ATTEMPT.reset(marker)
            if not attempt.submission_started:
                attempt.finish()


def serve(args):
    from vllm.entrypoints.cli.main import main
    from vllm.v1.engine.core_client import AsyncMPClient
    from vllm.version import __version__

    if __version__ != '0.22.1':
        raise RuntimeError('Guarded OvisOCR3 requires the verified vLLM 0.22.1 engine')
    install_engine_hooks(AsyncMPClient)
    sys.argv = ['vllm', 'serve', *args, '--api-server-count', '1', '--no-async-scheduling',
                '--distributed-executor-backend', 'uni', '--scheduler-cls', 'ovis_scheduler.OvisScheduler',
                '--middleware', 'vllm_guard.OvisExecutionMiddleware']
    main()
