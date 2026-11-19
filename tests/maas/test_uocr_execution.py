import asyncio
import importlib.util
from pathlib import Path
from threading import Lock
from types import SimpleNamespace

from fastapi import FastAPI, HTTPException, Request
from httpx import ASGITransport, AsyncClient
import pytest


TOKEN = 'task.' + 'e' * 32
PATH = '/v1/chat/completions'
HEADER = {'X-MaaS-Execution-Attempt': TOKEN}


@pytest.fixture
def runtime(monkeypatch):
    root = Path(__file__).resolve().parents[2]
    monkeypatch.syspath_prepend(str(root / 'gpu-ocr'))
    spec = importlib.util.spec_from_file_location('test_agent_uocr', root / 'deploy/unlimited-ocr-2/uocr_execution.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_one_grant_across_windows_replicas_and_client_cancellation(runtime):
    async def scenario():
        owner, events, probe = {}, [], asyncio.Event()
        lock = Lock()
        window_ended, next_window, released = asyncio.Event(), asyncio.Event(), asyncio.Event()

        class Authority:
            def report(self, token, *, finished):
                with lock:
                    if token in owner:
                        raise HTTPException(409, 'Already granted')
                    assert not finished
                    owner[token] = self

            def report_finished(self, token):
                assert owner[token] is self and released.is_set()
                events.append('receipt')

        def client_for_replica():
            class Chat:
                tokenizer_manager = SimpleNamespace(get_internal_state=self_check)

                async def handle_request(self, request, raw_request):
                    assert raw_request is None
                    events.append('first window')
                    window_ended.set()
                    await next_window.wait()
                    events.append('second window')
                    return {'document': 'test_agent_document'}

            execution = runtime.DocumentExecution(Authority())
            execution.install(Chat)
            app = FastAPI()
            app.add_middleware(runtime.DocumentExecutionMiddleware, execution=execution)

            @app.post(PATH)
            async def chat(request: Request):
                return await Chat().handle_request(
                    SimpleNamespace(stream=False, n=1, session_id=None, session_params=None), request)

            return AsyncClient(transport=ASGITransport(app=app), base_url='http://test'), execution

        async def self_check():
            probe.set()
            return [{runtime.IDLE: released.is_set()}]

        (first, execution), (other, _) = client_for_replica(), client_for_replica()
        async with first, other:
            assert (await first.post('/generate', headers=HEADER)).status_code == 404
            assert (await first.post(PATH)).status_code == 403
            request = asyncio.create_task(first.post(PATH, headers=HEADER))
            await asyncio.wait_for(window_ended.wait(), 2)
            assert (await other.post(PATH, headers=HEADER)).status_code == 409
            assert (await first.post(PATH, headers=HEADER)).status_code == 409
            assert events == ['first window']
            pending = execution.pending
            request.cancel()
            with pytest.raises(asyncio.CancelledError):
                await request
            next_window.set()
            await asyncio.wait_for(probe.wait(), 2)
            assert events == ['first window', 'second window'] and execution.unconfirmed
            released.set()
            await asyncio.wait_for(pending, 2)
            assert events == ['first window', 'second window', 'receipt']
            assert (await first.post(PATH, headers=HEADER)).status_code == 409

    asyncio.run(scenario())


def test_scheduler_readback_requires_idle_and_full_request_and_kv_pools(runtime):
    class Scheduler:
        token_to_kv_pool_allocator = SimpleNamespace(available_size=lambda: 7, size=9, page_size=2)
        req_to_token_pool = SimpleNamespace(available_size=lambda: 1, size=1)

        def is_fully_idle(self):
            return True

        def get_internal_state(self, _):
            return SimpleNamespace(internal_state={})

    synchronized = []
    runtime.install_scheduler_check(Scheduler, lambda: synchronized.append(True))
    scheduler = Scheduler()
    assert scheduler.get_internal_state(None).internal_state == {runtime.IDLE: False}
    assert synchronized == []
    scheduler.token_to_kv_pool_allocator.available_size = lambda: 8
    assert scheduler.get_internal_state(None).internal_state == {runtime.IDLE: True}
    assert synchronized == [True]
