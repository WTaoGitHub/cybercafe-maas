"""Bound JSON request bodies before framework parsing (including chunked requests)."""
import json, uuid


class JsonBodyLimit:
    def __init__(self, app, limit=65536, sync_limit=65536, task_limit=None):
        self.app, self.limit, self.sync_limit = app, limit, sync_limit
        self.task_limit = task_limit if task_limit is not None else limit

    async def __call__(self, scope, receive, send):
        if scope['type'] != 'http' or scope.get('method') != 'POST' or scope.get('path') == '/maas/v1/artifacts':
            return await self.app(scope,receive,send)
        async def too_large():
            rid=scope.get('state',{}).get('request_id') or 'req_'+uuid.uuid4().hex
            data=json.dumps({'error':{'code':'REQUEST_SIZE','message':'JSON request exceeds configured limit','retryable':False,'request_id':rid}}).encode()
            await send({'type':'http.response.start','status':413,'headers':[(b'content-type',b'application/json'),(b'x-request-id',rid.encode())]})
            await send({'type':'http.response.body','body':data})
        body=bytearray()
        while True:
            event=await receive()
            if event['type']=='http.disconnect':return
            body.extend(event.get('body',b''))
            limit=self.sync_limit if scope.get('path') in ('/v1/chat/completions','/ocr','/v1/ocr') else self.task_limit if scope.get('path')=='/maas/v1/tasks' else self.limit
            if len(body)>limit:
                await too_large();return
            if not event.get('more_body',False):break
        if scope.get('path')=='/maas/v1/tasks' and len(body)>self.limit:
            try:
                value=json.loads(body)
                image=value['input']['image']
                inline=isinstance(image,dict) and any(isinstance(image.get(k),str) for k in ('image_base64','data_url'))
            except (ValueError,KeyError,TypeError):inline=False
            if not inline:
                await too_large();return
        sent=False
        async def replay():
            nonlocal sent
            if not sent:
                sent=True;return {'type':'http.request','body':bytes(body),'more_body':False}
            return await receive()
        await self.app(scope,replay,send)
