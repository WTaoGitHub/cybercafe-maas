"""Synchronous OCR over the authenticated GPUStack gateway, one client send only."""
import base64
from datetime import datetime, timezone
import json
from pathlib import Path
import time
import httpx
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from .errors import ServiceError
from .versioning import VersionPin, verify_pin


class ImageReference(BaseModel):
    model_config = ConfigDict(extra='forbid')
    artifact_id: str = Field(min_length=1, max_length=80)


class OCRInput(BaseModel):
    model_config = ConfigDict(extra='forbid')
    image: ImageReference


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


def preflight(model):
    """Read only operator-produced metadata; customer keys never receive admin access."""
    try:
        pin = VersionPin(**json.loads(Path(model['pin_file']).read_text()))
        snapshot = json.loads(Path(model['preflight_file']).read_text())
        now = time.time()
        age = now-snapshot['observed_at']
        if not 0 <= age <= 30:
            raise ServiceError('PREFLIGHT_STALE', 'Refresh trusted control and gateway metadata', 503)
        if model['revision'] != pin.revision or model['upstream_model'] != pin.route_name:
            raise ServiceError('MODEL_REVISION_CHANGED', 'Catalog does not match immutable target', 409)
        if snapshot['gateway_max_attempts'] not in (1, 2, 3):
            raise ServiceError('GATEWAY_POLICY_UNVERIFIED', 'Gateway attempt bound is not approved', 503)
        verify_pin(pin, snapshot['version'], snapshot['route'], snapshot['targets'],
                   snapshot['model'], snapshot['backend'], snapshot['inventory'])
        return pin
    except ServiceError:
        raise
    except (OSError, ValueError, KeyError, TypeError):
        raise ServiceError('PREFLIGHT_UNAVAILABLE', 'Trusted version metadata unavailable', 503) from None


class OCRAdapter:
    replay_safe = False
    requires_upstream = True
    input_schema = OCRInput
    parameter_schema = OCRParameters

    def validate(self, payload, parameters):
        inp = OCRInput.model_validate(payload).model_dump()
        params = OCRParameters.model_validate(parameters).model_dump()
        return inp, params, inp['image']['artifact_id']

    def execute(self, ctx):
        model = ctx.model
        data, media = ctx.read_input()
        if media not in ('image/png', 'image/jpeg'):
            raise ServiceError('MEDIA_TYPE', 'OCR requires an uploaded PNG or JPEG', 415)
        preflight(model)
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
        with httpx.Client(timeout=httpx.Timeout(remaining, connect=min(5,remaining)),
                          trust_env=False, follow_redirects=False) as client:
            ctx.service.begin_backend(ctx.task)
            try:
                with client.stream('POST', ctx.service.settings.gpustack_url+'/v1/chat/completions',
                                   headers={'Authorization':'Bearer '+key}, json=body) as response:
                    raw = bytearray()
                    for chunk in response.iter_bytes(chunk_size=65536):
                        if len(raw)+len(chunk)>ctx.service.settings.max_output_bytes:
                            raise ServiceError('OUTPUT_SIZE', 'Inference response exceeds configured limit')
                        raw.extend(chunk)
                        if time.monotonic()>=ctx.deadline:
                            raise ServiceError('RUNTIME_EXCEEDED', 'Inference response exceeded runtime')
                    if response.status_code not in (200,400,401,403,404,413,415,422):
                        raise ServiceError('UNKNOWN_BACKEND_ACTIVITY', 'Gateway completion is unconfirmed')
                    ctx.service.complete_backend(ctx.task)
                    if response.status_code != 200:
                        raise ServiceError('UPSTREAM_REJECTED', 'Inference request rejected', 422)
            except httpx.HTTPError:
                raise ServiceError('UNKNOWN_BACKEND_ACTIVITY', 'Inference transport failed; dispatch quarantined') from None
        ctx.check()
        try:
            envelope = json.loads(raw)
            value = json.loads(envelope['choices'][0]['message']['content'])
            return OCRResult.model_validate(value).model_dump()
        except (ValueError, KeyError, IndexError, TypeError, ValidationError):
            raise ServiceError('OUTPUT_INVALID', 'OCR response does not match the approved schema') from None
