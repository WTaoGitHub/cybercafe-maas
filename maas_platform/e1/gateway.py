"""Bounded candidate transport over authenticated GPUStack worker aliases.

Caller retains the P4 queue, authorization and billing responsibilities. This
transport is deliberately not wired into P4's OCRAdapter or SyncIngress yet.
"""
import asyncio
import json
import httpx

from ..errors import ServiceError


class E1Gateway:
    def __init__(self, control, gpustack_url, authorize, max_output_bytes=20*1024*1024,
                 client_factory=httpx.AsyncClient):
        self.control = control
        self.url = gpustack_url.rstrip('/')+'/v1/chat/completions'
        self.authorize = authorize
        self.max_output_bytes = max_output_bytes
        self.client_factory = client_factory

    async def infer(self, token, identity, model, revision, body, kind='async', runtime=30):
        if (not isinstance(body, dict) or body.get('model') != model or body.get('stream',False) is not False
                or not isinstance(runtime,(int,float)) or not 0 < runtime <= 120):
            raise ServiceError('E1_INPUT', 'Use a logical model, stream=false and bounded runtime', 422)
        # Required callback must authorize the logical model/project and return
        # a registered inference key, NEVER the operator/collector admin key.
        # No worker, alias, headers, project or URL can be supplied in the body.
        key = self.authorize(identity,model)
        if not isinstance(key,str) or not key:
            raise ServiceError('E1_AUTH', 'A scoped inference credential is required', 403)
        intent = self.control.reserve(token,identity.project,model,revision,kind,self.control.clock()+runtime)
        confirmed = False
        budget = runtime
        try:
            async with asyncio.timeout(budget):
                async with self.client_factory(trust_env=False,follow_redirects=False,
                        timeout=httpx.Timeout(budget,connect=min(5,budget))) as client:
                    async with client.stream('POST',self.url,
                        headers={'Authorization':'Bearer '+key,'Accept-Encoding':'identity'},
                        json={**body,'model':intent['route']}) as response:
                        if response.headers.get('content-encoding','identity') != 'identity':
                            raise ServiceError('OUTPUT_ENCODING','Encoded response completion is unconfirmed',502)
                        raw = bytearray()
                        async for chunk in response.aiter_bytes():
                            if len(raw)+len(chunk)>self.max_output_bytes:
                                raise ServiceError('OUTPUT_SIZE','Response exceeded the configured bound',502)
                            raw.extend(chunk)
                        confirmed = response.status_code in (200,400,401,403,404,413,415,422)
                        if not confirmed:
                            raise ServiceError('UNKNOWN_BACKEND_ACTIVITY','Alias completion is unconfirmed',503)
                        if response.status_code != 200:
                            raise ServiceError('UPSTREAM_REJECTED','Alias rejected inference',response.status_code)
                        try:
                            return json.loads(raw)
                        except ValueError:
                            raise ServiceError('OUTPUT_INVALID','Invalid inference response JSON',502) from None
        except (httpx.HTTPError,TimeoutError):
            raise ServiceError('UNKNOWN_BACKEND_ACTIVITY','Worker dispatch completion is unconfirmed',503) from None
        finally:
            # Also executes on cancellation. A hard crash leaves a durable
            # intent that becomes UNKNOWN at deadline. Never replay the send.
            self.control.finish(token,confirmed)
