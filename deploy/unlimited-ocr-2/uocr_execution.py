"""Protect a whole document on the pinned Unlimited-OCR-2 SGLang worker."""

import asyncio
import hashlib
import importlib.util
import logging
import os
from pathlib import Path
import re
import subprocess

from fastapi import HTTPException
from starlette.datastructures import Headers
from starlette.responses import JSONResponse

from execution_guard import ATTEMPT_HEADER, ExecutionGuard


PREFIX = 'MAAS_UOCR_EXECUTION_'
IDLE = 'maas_document_idle'
LOG = logging.getLogger(__name__)
SOURCE_DIGESTS = {
    'entrypoints/http_server.py': '2463587265e6673dd15514ee9d6e0b55576c962333895876f04547d4c95dbf88',
    'entrypoints/openai/serving_chat.py': 'fb04303e2c2bd4f8fb30b79af7151ce62ea078136ce8eb1e02453bdd2efdca6c',
    'managers/scheduler.py': '4dd77987912f8451bcfac9a7711de6e5405ceaedd27f35f4bfef7222c1589e36',
    'managers/tokenizer_manager.py': 'a56042afafa445cc735e054a4ee0f70e61b5fa26b5638fc5865bd36d566926fa',
    'managers/tokenizer_control_mixin.py': 'd144a769569db8043e89d2c20313eccf4bf1b888a8fb50340b264d35ddd8241a',
}


async def wait_for_document_release(tokenizer):
    while True:
        states = await tokenizer.get_internal_state()
        if len(states) == 1 and states[0].get(IDLE) is True:
            return
        await asyncio.sleep(.05)


class DocumentExecution:
    def __init__(self, authority):
        self.authority = authority
        self.token = None
        self.pending = None
        self.unconfirmed = False

    def install(self, chat_type):
        native = chat_type.handle_request

        async def handle(serving, request, raw_request):
            if request.stream or request.n != 1 or request.session_id is not None or request.session_params is not None:
                raise HTTPException(422, 'Guarded OCR requires one non-streaming document without a user session')
            token = raw_request.headers.get(ATTEMPT_HEADER)
            if token is None or token != self.token:
                raise HTTPException(403, 'A MaaS task attempt is required')

            await asyncio.to_thread(self.authority.report, token, finished=False)
            self.unconfirmed = True
            # The accepted document owns all windows even after the caller leaves.
            response = await native(serving, request, None)
            try:
                await asyncio.wait_for(wait_for_document_release(serving.tokenizer_manager), timeout=5)
            except asyncio.TimeoutError:
                LOG.warning('Unlimited-OCR-2 scheduler did not confirm document release')
                raise HTTPException(503, 'GPU completion is unconfirmed') from None
            self.unconfirmed = False
            await asyncio.to_thread(self.authority.report_finished, token)
            return response

        chat_type.handle_request = handle


class DocumentExecutionMiddleware:
    def __init__(self, app, execution):
        self.app = app
        self.execution = execution

    async def __call__(self, scope, receive, send):
        if scope['type'] != 'http':
            await self.app(scope, receive, send)
            return
        path, method = scope['path'], scope['method']
        execution = self.execution
        if method == 'GET' and path in ('/health', '/v1/models', '/metrics', '/version'):
            if path == '/health' and execution.unconfirmed and execution.pending is None:
                await JSONResponse({'detail': 'GPU completion is unconfirmed'}, status_code=503)(scope, receive, send)
                return
            await self.app(scope, receive, send)
            return
        if path != '/v1/chat/completions' or method != 'POST':
            detail, status = 'Endpoint unavailable on guarded OCR service', 404
        else:
            tokens = Headers(scope=scope).getlist(ATTEMPT_HEADER)
            if len(tokens) != 1 or not re.fullmatch(r'task\.[0-9a-f]{32}', tokens[0]):
                detail, status = 'A MaaS task attempt is required', 403
            elif tokens[0] == execution.token:
                detail, status = 'Execution already in progress', 409
            elif execution.pending is not None or execution.unconfirmed:
                detail, status = 'Document worker unavailable', 503
            else:
                execution.token = tokens[0]
                execution.pending = asyncio.create_task(self.run(scope, receive, send))
                await asyncio.shield(execution.pending)
                return
        await JSONResponse({'detail': detail}, status_code=status)(scope, receive, send)

    async def run(self, scope, receive, send):
        try:
            await self.app(scope, receive, send)
        finally:
            self.execution.token = self.execution.pending = None


def install_scheduler_check(scheduler_type, synchronize):
    read_state = scheduler_type.get_internal_state

    def get_internal_state(scheduler, request):
        output = read_state(scheduler, request)
        kv = scheduler.token_to_kv_pool_allocator
        rows = scheduler.req_to_token_pool
        drained = (scheduler.is_fully_idle() and rows.available_size() == rows.size and
                   kv.available_size() == kv.size // kv.page_size * kv.page_size)
        if drained:
            synchronize()
        output.internal_state[IDLE] = drained
        return output

    scheduler_type.get_internal_state = get_internal_state


def run_scheduler(*args, **kwargs):
    from sglang.srt.managers.scheduler import Scheduler, run_scheduler_process
    from torch.cuda import synchronize

    install_scheduler_check(Scheduler, synchronize)
    run_scheduler_process(*args, **kwargs)


def serve(argv):
    root = Path(next(iter(importlib.util.find_spec('sglang').submodule_search_locations))) / 'srt'
    for name, digest in SOURCE_DIGESTS.items():
        if hashlib.sha256((root / name).read_bytes()).hexdigest() != digest:
            raise RuntimeError(f'Guarded Unlimited-OCR-2 requires the pinned M1 source: {name}')
    os.environ['SGLANG_UOCR_DOC_PREFETCH'] = '0'
    os.environ['SGLANG_ENABLE_HEALTH_ENDPOINT_GENERATION'] = '0'
    os.environ['SGLANG_RUST_SERVER'] = '0'

    from sglang.launch_server import load_plugins
    from sglang.srt.arg_groups.overrides import resolving_view
    from sglang.srt.entrypoints.http_server import app, launch_server
    from sglang.srt.entrypoints.openai.serving_chat import OpenAIServingChat
    from sglang.srt.server_args import prepare_server_args

    load_plugins()
    args = prepare_server_args([*argv, '--disable-overlap-schedule', '--skip-server-warmup'])
    args.resolve_once()
    config = resolving_view(args)
    if (config.nnodes != 1 or config.tp_size != 1 or config.dp_size != 1 or config.pp_size != 1 or
            config.tokenizer_worker_num != 1 or config.max_running_requests != 1 or
            config.disaggregation_mode != 'null' or not config.disable_radix_cache or
            not config.disable_overlap_schedule or config.chat_template != 'unlimited-ocr' or
            config.grpc_port is not None or config.grpc_mode or config.smg_grpc_mode):
        raise RuntimeError('Guarded Unlimited-OCR-2 requires the serial M1 profile')
    gpu = subprocess.check_output(['nvidia-smi', '--query-gpu=uuid', '--format=csv,noheader'], text=True).strip()
    execution = DocumentExecution(ExecutionGuard.from_env(gpu, PREFIX))
    execution.install(OpenAIServingChat)
    app.add_middleware(DocumentExecutionMiddleware, execution=execution)
    launch_server(args, run_scheduler_process_func=run_scheduler)
