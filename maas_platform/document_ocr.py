"""Single-send Unlimited-OCR-2 document adapter using the existing task lifecycle."""
import base64
import importlib.metadata
import hashlib
import json
from pathlib import Path
from pydantic import BaseModel, ConfigDict, Field
from .document_output import POSTPROCESSOR_REVISION, POSTPROCESSOR_SHA256
from .documents import prepare_document, parse_document
from .errors import ServiceError
from .ocr_connector import ImageReference, OCRAdapter

MODEL_REVISION = '0480966938291445591f69f08a13b041c073a96a'
RUNTIME_DIGEST = 'sha256:0a4ee2f9351225fe2ff91fe1d7f638b86972b9b2f704ab848325f2d0bc0d2fa6'
GUARDED_RUNTIME_DIGEST = 'sha256:9139bd41a0476782acee4be89363a5c1188c9910ed9211ce84d06920ac582615'
WITNESSED_RUNTIME_DIGEST = 'sha256:a7c8520bb771fdf75248cb75ebf57fe072558960dbdf9b0a25573c6017cb156f'
PROFILE = 'pdfium5.15.2-pages10-pixels50m-json-v1'
MAX_RENDERED_REQUEST_BYTES = 96 * 1024**2


class DocumentInput(BaseModel):
    model_config = ConfigDict(extra='forbid')
    document: ImageReference


class DocumentParameters(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    dpi: int = Field(default=150, ge=72, le=300)


class DocumentOCRAdapter(OCRAdapter):
    input_schema = DocumentInput
    parameter_schema = DocumentParameters
    input_limits = {'max_bytes': 10 * 1024**2, 'max_pdf_pages': 10,
        'default_dpi': 150, 'max_dpi': 300, 'max_pdf_page_pixels': 10_000_000,
        'max_total_pixels': 50_000_000, 'max_image_pixels': 20_000_000,
        'default_timeout_seconds': 900, 'max_attempts': 1, 'max_running_documents': 1}

    def validate(self, payload, parameters):
        inp = self.input_schema.model_validate(payload).model_dump()
        params = self.parameter_schema.model_validate(parameters).model_dump()
        return inp, params, inp['document']['artifact_id']

    def validate_model(self, model):
        required = {'model_revision': MODEL_REVISION,
                    'postprocessor_revision': POSTPROCESSOR_REVISION, 'document_profile': PROFILE}
        # Older pilots add the guard to the base image with read-only file mounts.
        approved_runtimes = (RUNTIME_DIGEST, GUARDED_RUNTIME_DIGEST, WITNESSED_RUNTIME_DIGEST) if model.get('execution_guard') is True else (RUNTIME_DIGEST,)
        if (any(model.get(k) != v for k, v in required.items())
                or model.get('runtime_image_digest') not in approved_runtimes or model['max_runtime_s'] > 600):
            raise ValueError('Document catalog must pin the approved model, runtime, postprocessor and profile')
        try:
            version = importlib.metadata.version('pypdfium2')
        except importlib.metadata.PackageNotFoundError:
            raise ValueError('Document renderer dependency is not installed') from None
        if version != '5.15.2':
            raise ValueError('Document renderer version differs from the approved profile')
        if hashlib.sha256(Path(__file__).with_name('_unlimited_postprocess.py').read_bytes()).hexdigest() != POSTPROCESSOR_SHA256:
            raise ValueError('Document postprocessor differs from the approved source')

    def _prepare(self, ctx):
        data, media = ctx.read_input()
        with prepare_document(data, media, ctx.task['parameters']['dpi'], ctx.check) as (root, metadata):
            content, pages = [], []
            for page in metadata['pages']:
                ctx.check()
                raw = (root / page['filename']).read_bytes()
                content.append({'type': 'image_url', 'image_url': {'url':
                    'data:' + page['media_type'] + ';base64,' + base64.b64encode(raw).decode('ascii')}})
                pages.append({k: v for k, v in page.items() if k not in ('filename', 'media_type')})
        count = len(pages)
        content.append({'type': 'text', 'text': '<PAGE><image>' * count + 'Multi page parsing.'})
        body = {'model': ctx.model['upstream_model'], 'stream': False,
            'messages': [{'role': 'user', 'content': content}], 'max_tokens': 20000 * count,
            'temperature': 0, 'skip_special_tokens': False, 'images_config': {'crop_mode': True},
            'custom_logit_processor': 'maas.unlimited-ocr-2.ngram.v1',
            'custom_params': {'ngram_size': 35, 'window_size': 1024 if count > 1 else 128,
                             'page_token_id': 128827, 'eos_token_id': 1, 'max_page_tokens': 20000}}
        encoded = json.dumps(body).encode()
        if len(encoded) > MAX_RENDERED_REQUEST_BYTES:
            raise ServiceError('DOCUMENT_RENDER_SIZE', 'Rendered document request exceeds its budget', 413)
        return encoded, pages

    def _decode(self, ctx, raw, pages):
        ctx.check()
        try:
            envelope = json.loads(raw)
        except ValueError:
            raise ServiceError('OUTPUT_INVALID', 'Document response must be JSON', 422) from None
        provenance = {k: ctx.model[k] for k in ('model_revision', 'runtime_image_digest', 'document_profile')}
        provenance.update({'postprocessor_revision': POSTPROCESSOR_REVISION,
                           'postprocessor_sha256': POSTPROCESSOR_SHA256})
        output = parse_document(json.dumps({'envelope': envelope, 'pages': pages,
            'provenance': provenance, 'parameters': ctx.task['parameters']}).encode(), ctx.check)
        ctx.metering_pages = output['page_count']
        ctx.metering_tokens = {'status': 'unavailable', 'input_tokens': None, 'output_tokens': None,
            'source': None, 'reason': 'DOCUMENT_WINDOW_USAGE_UNVERIFIED'}
        return output
