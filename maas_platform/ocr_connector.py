"""Synchronous OCR over the authenticated GPUStack gateway, one client send only."""
import asyncio
import base64
from datetime import datetime, timezone
import json
import time
import httpx
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from .errors import ServiceError


class ImageReference(BaseModel):
    model_config = ConfigDict(extra='forbid')
    artifact_id: str = Field(min_length=1, max_length=80)


class InlineImage(BaseModel):
    model_config = ConfigDict(extra='forbid')
    data_url: str = Field(min_length=1, description='Strict PNG/JPEG base64 data URL; exclusive with artifact_id')


class URLImage(BaseModel):
    model_config = ConfigDict(extra='forbid')
    url: str = Field(min_length=1,max_length=4096,description='Public HTTP(S) PNG/JPEG; downloaded before task acceptance')


class OCRInput(BaseModel):
    model_config = ConfigDict(extra='forbid')
    image: ImageReference | InlineImage | URLImage


class OCRParameters(BaseModel):
    model_config = ConfigDict(extra='forbid')


class OCRLine(BaseModel):
    box: list[list[float]]
    text: str
    score: float = Field(ge=0, le=1, allow_inf_nan=False)


class OCRResult(BaseModel):
    text: str
    lines: list[OCRLine]
    elapsed_ms: float = Field(ge=0, allow_inf_nan=False)
    node: str




class OCRAdapter:
    replay_safe = False
    requires_upstream = True
    input_schema = OCRInput
    parameter_schema = OCRParameters

    def validate(self, payload, parameters):
        inp = OCRInput.model_validate(payload).model_dump()
        params = OCRParameters.model_validate(parameters).model_dump()
        return inp, params, inp['image'].get('artifact_id')

    def execute(self, ctx):
        model = ctx.model
        data, media = ctx.read_input()
        if media not in ('image/png', 'image/jpeg'):
            raise ServiceError('MEDIA_TYPE', 'OCR requires an uploaded PNG or JPEG', 415)
        key = ctx.service.auth.key_for(ctx.identity)
        ctx.check()
        remaining = min(ctx.deadline-time.monotonic(),
                        (ctx.task['deadline']-datetime.now(timezone.utc)).total_seconds())
        if remaining <= 0:
            raise ServiceError('RUNTIME_EXCEEDED', 'No inference time remains')
        body = {'model': model['upstream_model'], 'stream': False, 'messages': [{'role':'user','content':[
            {'type':'image_url','image_url':{'url':'data:'+media+';base64,'+base64.b64encode(data).decode()}}]}]}
        # No redirects, proxy environment, cookie jar or client-side transport retries.
        # Higress may still try up to three backends; no application replay after send.
        network_deadline = time.monotonic()+remaining
        ctx.service.begin_backend(ctx.task)
        raw = asyncio.run(self._infer(ctx, key, body, max(0,network_deadline-time.monotonic())))
        ctx.check()
        try:
            envelope = json.loads(raw)
            value = json.loads(envelope['choices'][0]['message']['content'])
            return OCRResult.model_validate(value).model_dump()
        except (ValueError, KeyError, IndexError, TypeError, ValidationError):
            raise ServiceError('OUTPUT_INVALID', 'OCR response does not match the approved schema') from None

    async def _infer(self, ctx, key, body, remaining):
        # asyncio.timeout is a wall-clock deadline, including slow/trickling bodies.
        budget = min(remaining, ctx.deadline-time.monotonic())
        try:
            async with asyncio.timeout(max(0, budget)):
                async with httpx.AsyncClient(timeout=httpx.Timeout(max(.001,budget), connect=min(5,max(.001,budget))),
                                             trust_env=False, follow_redirects=False) as client:
                    async with client.stream('POST', ctx.service.settings.gpustack_url+'/v1/chat/completions',
                                             headers={'Authorization':'Bearer '+key,'Accept-Encoding':'identity'}, json=body) as response:
                        if response.headers.get('content-encoding','identity') != 'identity':
                            raise ServiceError('OUTPUT_ENCODING', 'Compressed inference responses are unsupported')
                        raw = bytearray()
                        async for chunk in response.aiter_bytes():
                            if len(raw)+len(chunk)>ctx.service.settings.max_output_bytes:
                                raise ServiceError('OUTPUT_SIZE', 'Inference response exceeds configured limit')
                            raw.extend(chunk)
                        if response.status_code not in (200,400,401,403,404,413,415,422):
                            raise ServiceError('UNKNOWN_BACKEND_ACTIVITY', 'Gateway completion is unconfirmed')
                        ctx.service.complete_backend(ctx.task)
                        if response.status_code != 200:
                            raise ServiceError('UPSTREAM_REJECTED', 'Inference request rejected', 422)
                        return raw
        except (httpx.HTTPError, TimeoutError):
            raise ServiceError('UNKNOWN_BACKEND_ACTIVITY', 'Inference transport failed; dispatch quarantined') from None
