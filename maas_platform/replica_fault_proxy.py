"""Explicitly invoked, loopback-only fault transport for isolated replica trials.

Not imported by the API, collector or production controller. No control HTTP
endpoint: the owning test process changes the in-memory gate directly.
"""
import asyncio
from hmac import compare_digest
import json
from urllib.parse import urlsplit

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response, StreamingResponse
import httpx

from .replica_apply import model_settings


class FaultGate:
    MODES = {'normal', 'watch_down', 'reads_down', 'incomplete_lists',
             'before_commit', 'after_commit'}

    def __init__(self):
        self.mode = 'normal'
        self.confirm_blocked = False
        self.cut = asyncio.Event()
        self.received_puts = self.forwarded_puts = self.committed_puts = 0
        self.watch_connections = self.watch_rejections = self.read_rejections = 0

    def change(self, mode):
        if mode not in self.MODES:
            raise ValueError('Unknown fault mode')
        self.mode = mode
        if mode == 'watch_down':
            self.cut.set()
        else:
            self.cut = asyncio.Event()
        if mode == 'normal':
            self.confirm_blocked = False

    def counts(self):
        return {key: getattr(self, key) for key in (
            'received_puts', 'forwarded_puts', 'committed_puts',
            'watch_connections', 'watch_rejections', 'read_rejections')}


def loopback(url):
    parsed = urlsplit(url)
    if (parsed.scheme != 'http' or parsed.hostname != '127.0.0.1'
            or not parsed.port or parsed.username or parsed.password
            or parsed.path not in ('', '/') or parsed.query or parsed.fragment):
        raise ValueError('Fault trial requires a loopback native endpoint')
    return url.rstrip('/')


def application(upstream, key, model_id, gate, *, response_delay_s=6):
    """Forward only native reads and a checked +1 for one explicit deployment."""
    if type(model_id) is not int or model_id < 1 or not key:
        raise ValueError('Explicit trial identity and private key required')
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    lists = {'/v2/workers', '/v2/models', '/v2/model-instances'}
    model_path = f'/v2/models/{model_id}'

    @app.api_route('/{path:path}', methods=['GET', 'POST', 'PUT', 'PATCH', 'DELETE'])
    async def proxy(request: Request):
        if not compare_digest(request.headers.get('authorization', ''), 'Bearer '+key):
            return Response(status_code=401)
        path = '/'+request.path_params['path']
        method = request.method
        watch = request.query_params.get('watch') == 'true'
        if path not in lists | {'/version', model_path}:
            return Response(status_code=403)
        if method not in ('GET', 'PUT') or (method == 'PUT' and (path != model_path or request.url.query)):
            return Response(status_code=403)
        if method == 'GET':
            if watch and (path not in lists or set(request.query_params) != {'watch'}):
                return Response(status_code=403)
            if watch and gate.mode == 'watch_down':
                gate.watch_rejections += 1
                return Response(status_code=503)
            if not watch and path != '/version' and (gate.mode == 'reads_down' or gate.confirm_blocked):
                gate.read_rejections += 1
                return Response(status_code=503)
            if watch:
                response = await upstream.send(upstream.build_request('GET', path,
                                               params=request.query_params), stream=True)
                if response.status_code != 200:
                    await response.aclose()
                    return Response(status_code=response.status_code)
                gate.watch_connections += 1
                cut = gate.cut

                async def stream():
                    iterator = response.aiter_bytes().__aiter__()
                    next_chunk = interrupted = None
                    try:
                        while True:
                            next_chunk = asyncio.create_task(iterator.__anext__())
                            interrupted = asyncio.create_task(cut.wait())
                            done, _ = await asyncio.wait((next_chunk, interrupted),
                                                         return_when=asyncio.FIRST_COMPLETED)
                            if interrupted in done:
                                return
                            interrupted.cancel()
                            await asyncio.gather(interrupted, return_exceptions=True)
                            try:
                                yield next_chunk.result()
                            except StopAsyncIteration:
                                return
                    finally:
                        tasks = [task for task in (next_chunk, interrupted) if task is not None]
                        for task in tasks:
                            task.cancel()
                        await asyncio.gather(*tasks, return_exceptions=True)
                        await response.aclose()

                return StreamingResponse(stream(), media_type=response.headers.get('content-type'))
            response = await upstream.get(path, params=request.query_params)
            if gate.mode == 'incomplete_lists' and path in lists and response.status_code == 200:
                body = response.json()
                body['pagination']['total'] += 1
                return JSONResponse(body)
            return Response(response.content, response.status_code,
                            media_type=response.headers.get('content-type'))

        gate.received_puts += 1
        if gate.mode == 'reads_down' or gate.confirm_blocked:
            return Response(status_code=503)
        try:
            body = json.loads(await request.body())
            fresh = await upstream.get(model_path)
            fresh.raise_for_status()
            row = fresh.json()
            if (type(body.get('replicas')) is not int or body['replicas'] != row['replicas']+1
                    or body['replicas'] != 1 or body != {**model_settings(row), 'replicas': 1}):
                return Response(status_code=403)
        except (ValueError, KeyError, TypeError):
            return Response(status_code=403)
        mode = gate.mode  # A delayed fault must never forward after being cleared.
        if mode == 'before_commit':
            gate.confirm_blocked = True
            await asyncio.sleep(response_delay_s)
            return Response(status_code=504)
        gate.forwarded_puts += 1
        response = await upstream.put(model_path, json=body)
        if response.status_code == 200:
            gate.committed_puts += 1
        if mode == 'after_commit' and response.status_code == 200:
            gate.confirm_blocked = True
            await asyncio.sleep(response_delay_s)
            return Response(status_code=504)
        return Response(response.content, response.status_code,
                        media_type=response.headers.get('content-type'))

    return app
