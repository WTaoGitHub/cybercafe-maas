"""OCR over the authenticated GPUStack gateway, one client send only."""
import asyncio
import base64
from datetime import datetime, timezone
from http.cookiejar import CookieJar, DefaultCookiePolicy
import json
import time
import httpx
from pydantic import BaseModel, ConfigDict, Field, ValidationError, AliasChoices, StrictBool
from .errors import ServiceError
from .pp_ocr_execution import ATTEMPT_HEADER, finished


class ImageReference(BaseModel):
    model_config = ConfigDict(extra='forbid')
    artifact_id: str = Field(min_length=1, max_length=80)


class InlineImage(BaseModel):
    model_config = ConfigDict(extra='forbid')
    image_base64: str = Field(min_length=1, validation_alias=AliasChoices('image_base64','data_url'), description='PNG/JPEG strict raw base64 or data URL; exclusive with artifact_id/image_url. Legacy data_url is deprecated.')


class URLImage(BaseModel):
    model_config = ConfigDict(extra='forbid')
    image_url: str = Field(min_length=1,max_length=4096,validation_alias=AliasChoices('image_url','url'),description='Public HTTP(S) PNG/JPEG; downloaded before task acceptance. Legacy url is deprecated.')


class OCRInput(BaseModel):
    model_config = ConfigDict(extra='forbid')
    image: ImageReference | InlineImage | URLImage


class OCRParameters(BaseModel):
    model_config = ConfigDict(extra='forbid')
    return_boxes: StrictBool = True


class OCRLine(BaseModel):
    box: list[list[float]]
    text: str
    score: float = Field(ge=0, le=1, allow_inf_nan=False)


class OCRResult(BaseModel):
    text: str
    lines: list[OCRLine]
    elapsed_ms: float = Field(ge=0, allow_inf_nan=False)
    node: str


class RejectCookies(DefaultCookiePolicy):
    def set_ok(self, cookie, request):
        return False


def inference_client(slots=1):
    limits = httpx.Limits(max_connections=slots, max_keepalive_connections=slots)
    return httpx.AsyncClient(trust_env=False, follow_redirects=False, limits=limits,
                             cookies=CookieJar(policy=RejectCookies()))


class OCRAdapter:
    replay_safe = False
    requires_upstream = True
    input_schema = OCRInput
    parameter_schema = OCRParameters

    def validate(self, payload, parameters):
        inp = OCRInput.model_validate(payload).model_dump()
        params = OCRParameters.model_validate(parameters).model_dump(exclude_defaults=True)
        return inp, params, inp['image'].get('artifact_id')

    def execute(self, ctx):
        return asyncio.run(self.execute_async(ctx))

    async def execute_async(self, ctx, client=None, draining=None):
        body, metadata = await asyncio.to_thread(self._prepare, ctx)
        key, deadline = await asyncio.to_thread(self._authorize, ctx, draining)
        raw = await self._infer(ctx, key, body, max(0, deadline-time.monotonic()), client=client)
        return await asyncio.to_thread(self._decode, ctx, raw, metadata)

    def _prepare(self, ctx):
        body = {'model': ctx.model['upstream_model'], 'stream': False, 'messages': [{'role':'user','content':[
            self._image_part(ctx)]}]}
        return json.dumps(body).encode(), None

    @staticmethod
    def _image_part(ctx):
        data, media = ctx.read_input()
        if media not in ('image/png', 'image/jpeg'):
            raise ServiceError('MEDIA_TYPE', 'OCR requires an uploaded PNG or JPEG', 415)
        return {'type':'image_url','image_url':{'url':'data:'+media+';base64,'+base64.b64encode(data).decode()}}

    @staticmethod
    def _authorize(ctx, draining):
        key = ctx.service.auth.key_for(ctx.identity)
        ctx.check()
        now = time.monotonic()
        deadline = min(ctx.deadline, now + (ctx.task['deadline']-datetime.now(timezone.utc)).total_seconds())
        if deadline <= now:
            raise ServiceError('RUNTIME_EXCEEDED', 'No inference time remains')
        if draining is None:
            ctx.service.begin_backend(ctx.task)
        else:
            ctx.service.begin_backend(ctx.task, draining=draining)
        return key, deadline

    def _decode(self, ctx, raw, metadata):
        ctx.check()
        try:
            envelope = json.loads(raw)
            value = json.loads(envelope['choices'][0]['message']['content'])
            output = OCRResult.model_validate(value).model_dump()
            if not ctx.task.get('parameters', {}).get('return_boxes', True):
                for line in output['lines']:
                    del line['box']
            from .metering import tokens_from_response
            ctx.metering_tokens = tokens_from_response(envelope)
            return output
        except (ValueError, KeyError, IndexError, TypeError, ValidationError):
            raise ServiceError('OUTPUT_INVALID', 'OCR response does not match the approved schema') from None

    async def _infer(self, ctx, key, body, remaining, client=None):
        # asyncio.timeout is a wall-clock deadline, including slow/trickling bodies.
        budget = min(remaining, ctx.deadline-time.monotonic())
        if client is None:
            started = time.monotonic()
            async with inference_client() as private_client:
                return await self._infer(ctx, key, body, budget-(time.monotonic()-started), client=private_client)
        timeout = httpx.Timeout(max(.001,budget), connect=min(5,max(.001,budget)))
        headers = {'Authorization': 'Bearer '+key, 'Accept-Encoding': 'identity', 'Content-Type': 'application/json'}
        guarded = ctx.model.get('execution_guard', False)
        if guarded:
            headers[ATTEMPT_HEADER] = 'task.' + ctx.task['token']
        try:
            async with asyncio.timeout(max(0, budget)):
                # Higress can try multiple backends. MaaS sends once and records the outcome.
                async with client.stream('POST', ctx.service.settings.gpustack_url+'/v1/chat/completions',
                                         headers=headers, content=body, timeout=timeout) as response:
                    if response.headers.get('content-encoding','identity') != 'identity':
                        raise ServiceError('OUTPUT_ENCODING', 'Compressed inference responses are unsupported')
                    raw = bytearray()
                    async for chunk in response.aiter_bytes():
                        if len(raw)+len(chunk)>ctx.service.settings.max_output_bytes:
                            raise ServiceError('OUTPUT_SIZE', 'Inference response exceeds configured limit')
                        raw.extend(chunk)
                    status = response.status_code
        except (httpx.HTTPError, TimeoutError):
            if guarded and await asyncio.to_thread(finished, ctx.service.db, 'task', ctx.task['token']):
                raise ServiceError('BACKEND_RESULT_UNAVAILABLE', 'GPU finished but its response was unavailable') from None
            raise ServiceError('UNKNOWN_BACKEND_ACTIVITY', 'Inference transport failed; dispatch quarantined') from None
        if guarded and not await asyncio.to_thread(finished, ctx.service.db, 'task', ctx.task['token']):
            raise ServiceError('UNKNOWN_BACKEND_ACTIVITY', 'GPU completion is unconfirmed')
        if status not in (200,400,401,403,404,413,415,422):
            if guarded:
                raise ServiceError('BACKEND_RESULT_UNAVAILABLE', 'GPU finished but its response was unavailable')
            raise ServiceError('UNKNOWN_BACKEND_ACTIVITY', 'Gateway completion is unconfirmed')
        if not guarded:
            await asyncio.to_thread(ctx.service.complete_backend, ctx.task)
        if status != 200:
            raise ServiceError('UPSTREAM_REJECTED', 'Inference request rejected', 422)
        return raw
