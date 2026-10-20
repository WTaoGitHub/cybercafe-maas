"""Bound JSON request bodies before framework parsing (including chunked requests)."""
import json, uuid


class JsonBodyLimit:
    def __init__(self, app, limit=65536):
        self.app, self.limit = app, limit

    async def __call__(self, scope, receive, send):
        if scope['type'] != 'http' or scope.get('method') != 'POST' or scope.get('path') == '/maas/v1/artifacts':
            return await self.app(scope,receive,send)
        body=bytearray()
        while True:
            event=await receive()
            if event['type']=='http.disconnect':return
            body.extend(event.get('body',b''))
            if len(body)>self.limit:
                rid=scope.get('state',{}).get('request_id') or 'req_'+uuid.uuid4().hex
                data=json.dumps({'error':{'code':'REQUEST_SIZE','message':'JSON request exceeds 64 KiB','retryable':False,'request_id':rid}}).encode()
                await send({'type':'http.response.start','status':413,'headers':[(b'content-type',b'application/json'),(b'x-request-id',rid.encode())]})
                await send({'type':'http.response.body','body':data});return
            if not event.get('more_body',False):break
        sent=False
        async def replay():
            nonlocal sent
            if not sent:
                sent=True;return {'type':'http.request','body':bytes(body),'more_body':False}
            return await receive()
        await self.app(scope,replay,send)
