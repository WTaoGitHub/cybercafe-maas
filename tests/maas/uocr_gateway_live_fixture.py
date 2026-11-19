"""Instrument a disposable guarded Unlimited-OCR-2 endpoint for a gateway trial."""

from hashlib import sha256
from pathlib import Path
import sys

from starlette.datastructures import Headers
from starlette.responses import JSONResponse


CASE = 'X-MaaS-UOCR-Probe'
MARKER = 'X-MaaS-Execution-Attempt'
CASES = {'normal', 'retry-small', 'retry-large', 'terminal-503', 'disconnect', 'client-timeout', 'gateway-timeout'}
ERROR = {'error': {'message': 'Injected Unlimited-OCR-2 probe failure', 'type': 'server_error'}}


class GatewayProbe:
    def __init__(self, app):
        self.app = app
        self.events = {case: [] for case in CASES}

    async def __call__(self, scope, receive, send):
        if scope['type'] != 'http':
            await self.app(scope, receive, send)
            return
        if scope['method'] == 'GET' and scope['path'] == '/probe/stats':
            await JSONResponse(self.events)(scope, receive, send)
            return
        if scope['method'] != 'POST' or scope['path'] != '/v1/chat/completions':
            await self.app(scope, receive, send)
            return

        headers = Headers(scope=scope)
        case = headers.get(CASE)
        if case not in self.events:
            await JSONResponse({'detail': 'A reserved synthetic probe case is required'}, 400)(scope, receive, send)
            return
        attempts = self.events[case]
        if len(attempts) >= 20:
            await JSONResponse({'detail': 'Probe attempt ceiling reached'}, 429)(scope, receive, send)
            return
        event = {'markers': [sha256(value.encode()).hexdigest() for value in headers.getlist(MARKER)], 'bytes': 0}
        attempts.append(event)
        body_hash = sha256()

        async def observed_receive():
            message = await receive()
            if message['type'] == 'http.request':
                chunk = message.get('body', b'')
                event['bytes'] += len(chunk)
                body_hash.update(chunk)
                if not message.get('more_body', False):
                    event['body_sha256'] = body_hash.hexdigest()
            return message

        if case in ('retry-small', 'retry-large'):
            while True:
                message = await observed_receive()
                if message['type'] == 'http.disconnect':
                    return
                if event['bytes'] > 16 * 1024 * 1024:
                    event['wire_status'] = 413
                    await JSONResponse({'detail': 'Probe size ceiling reached'}, 413)(scope, receive, send)
                    return
                if not message.get('more_body', False):
                    break
            event['wire_status'] = 503
            await JSONResponse(ERROR, 503)(scope, receive, send)
            return

        discard = False

        async def observed_send(message):
            nonlocal discard
            if message['type'] == 'http.response.start':
                event['engine_status'] = message['status']
                discard = case == 'terminal-503' and message['status'] == 200
                event['wire_status'] = 503 if discard else message['status']
                if discard:
                    # The stock middleware only returns 200 after the authority commits the terminal receipt.
                    await JSONResponse(ERROR, 503)(scope, receive, send)
            if not discard:
                await send(message)

        await self.app(scope, observed_receive, observed_send)


def instrument(source):
    marker = '    app.add_middleware(DocumentExecutionMiddleware, execution=execution)'
    assert source.count(marker) == 1
    return source.replace(marker, marker + '\n    from uocr_gateway_live_fixture import GatewayProbe\n'
                          '    app.add_middleware(GatewayProbe)')


if __name__ == '__main__':
    launcher = Path(__file__).resolve().parents[2] / 'deploy/unlimited-ocr-2/uocr_execution.py'
    sys.stdout.write(instrument(launcher.read_text()))
