import asyncio
import importlib.util
from pathlib import Path
from threading import Lock
from types import SimpleNamespace

from fastapi import FastAPI, HTTPException
from httpx import ASGITransport, AsyncClient
import pytest


TOKEN = 'task.' + 'e' * 32
HEADER = {'X-MaaS-Execution-Attempt': TOKEN}


@pytest.fixture
def runtime(monkeypatch):
    root = Path(__file__).resolve().parents[2]
    monkeypatch.syspath_prepend(str(root / 'gpu-ocr'))
    spec = importlib.util.spec_from_file_location('test_ovis_runtime', root / 'deploy/ovisocr3/vllm_guard.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    ledger, lock = {}, Lock()

    class Guard:
        def __init__(self, gpu):
            self.gpu = gpu

        def report(self, attempt, *, finished):
            assert not finished
            with lock:
                if attempt in ledger:
                    raise HTTPException(409, 'Already granted')
                ledger[attempt] = (self.gpu, False)

        def report_finished(self, attempt):
            with lock:
                assert ledger[attempt] == (self.gpu, False)
                ledger[attempt] = (self.gpu, True)

    class Core:
        def __init__(self):
            self.submitted = asyncio.Event()
            self.requests = []

        async def add_request_async(self, request):
            self.requests.append(request.request_id)
            self.submitted.set()

        async def signal(self, terminal=(), frontend_finished=False):
            outputs = [SimpleNamespace(request_id='internal', finished=True)] if frontend_finished else []
            await self.process_engine_outputs(SimpleNamespace(finished_requests=terminal, outputs=outputs))

    module.install_engine_hooks(Core)
    monkeypatch.setattr(module.ExecutionGuard, 'from_env', lambda gpu, _: Guard(gpu))
    gpu_ids = iter(['gpu1', 'gpu2'])
    monkeypatch.setattr(module.subprocess, 'check_output', lambda *_, **__: next(gpu_ids))
    return module, Core, ledger


def client_for(module, core, *, return_from_http=None):
    app = FastAPI()
    app.add_middleware(module.OvisExecutionMiddleware)

    @app.post('/v1/chat/completions')
    async def completion(body: dict):
        assert body['model'] == 'test_agent_ovis'
        await core.add_request_async(SimpleNamespace(request_id='internal'))
        if return_from_http:
            await return_from_http.wait()
        return {'choices': [{'message': {'content': 'test_agent_ovis'}}]}

    return AsyncClient(transport=ASGITransport(app=app), base_url='http://test')


def request(client, **body):
    return client.post('/v1/chat/completions', headers=HEADER, json={'model': 'test_agent_ovis', **body})


def test_two_replicas_hold_response_until_exact_core_terminal(runtime):
    module, Core, ledger = runtime

    async def race():
        left, right = Core(), Core()
        async with client_for(module, left) as first, client_for(module, right) as second:
            response = asyncio.create_task(request(first))
            await asyncio.wait_for(left.submitted.wait(), 1)
            assert (await request(second)).status_code == 409
            assert left.requests == ['internal'] and right.requests == []
            await left.signal(frontend_finished=True)
            await right.signal({'internal'})
            await asyncio.sleep(0)
            assert not response.done() and ledger[TOKEN] == ('gpu1', False)
            await left.signal({'internal'})
            assert (await asyncio.wait_for(response, 1)).status_code == 200
            assert ledger[TOKEN] == ('gpu1', True)
            await left.signal({'internal'})

    asyncio.run(race())


def test_cancellation_waits_for_finished_only_engine_envelope(runtime):
    module, Core, ledger = runtime

    async def disconnect():
        core = Core()
        async with client_for(module, core, return_from_http=asyncio.Event()) as client:
            response = asyncio.create_task(request(client))
            await asyncio.wait_for(core.submitted.wait(), 1)
            response.cancel()
            with pytest.raises(asyncio.CancelledError):
                await response
            assert ledger[TOKEN] == ('gpu1', False)
            await core.signal({'internal'})
            await asyncio.wait_for(asyncio.gather(*module.KEEPALIVE), 1)
            assert ledger[TOKEN] == ('gpu1', True)

    asyncio.run(disconnect())


def test_unsupported_requests_never_enter_the_engine(runtime):
    module, Core, ledger = runtime

    async def reject():
        core = Core()
        async with client_for(module, core) as client:
            assert (await client.post('/v1/completions', headers=HEADER, json={})).status_code == 404
            assert (await client.post('/v1/chat/completions', json={})).status_code == 403
            assert (await request(client, stream=True)).status_code == 422
            assert (await request(client, n=2)).status_code == 422
            assert core.requests == [] and ledger == {}

    asyncio.run(reject())


def test_pre_dispatch_completion_fences_delayed_core_submission(runtime):
    module, Core, ledger = runtime

    async def check():
        core = Core()
        grant = module.ExecutionGuard.from_env('gpu', '')
        grant.report(TOKEN, finished=False)
        attempt = module.Attempt(grant, TOKEN)
        marker = module.ATTEMPT.set(attempt)
        try:
            attempt.finish()
            with pytest.raises(RuntimeError, match='unused MaaS authorization'):
                await core.add_request_async(SimpleNamespace(request_id='too_late'))
            assert await asyncio.wait_for(attempt.receipt, 1)
            assert ledger[TOKEN] == ('gpu', True) and core.requests == []
        finally:
            module.ATTEMPT.reset(marker)

    asyncio.run(check())
