"""Single-image OvisOCR3 plain-text profile over the existing GPUStack transport."""
import json

from pydantic import BaseModel, ConfigDict

from .errors import ServiceError
from .metering import tokens_from_response
from .ocr_connector import ImageReference, OCRAdapter


PLAIN_OCR_PROMPT = (
    'Read all the text in the image. Directly output the content.\n\n'
    'Rules:\n'
    '1. Preserve the original text exactly without translation, paraphrasing, correction, or completion.\n'
    '2. Follow the natural human reading order.\n'
    '3. Output plain text only. Use spaces and line breaks only to preserve text separation and reading order.\n'
    '4. Do not output Markdown, HTML, JSON, bounding boxes, category labels, explanations, or code fences.'
)


class OvisOCRInput(BaseModel):
    model_config = ConfigDict(extra='forbid')
    image: ImageReference


class OvisOCRParameters(BaseModel):
    model_config = ConfigDict(extra='forbid')


class OvisOCRAdapter(OCRAdapter):
    input_schema = OvisOCRInput
    parameter_schema = OvisOCRParameters

    def _prepare(self, ctx):
        body = {'model': ctx.model['upstream_model'], 'stream': False, 'temperature': 0, 'max_tokens': 4096,
                'chat_template_kwargs': {'enable_thinking': False}, 'messages': [{'role': 'user', 'content': [
                    self._image_part(ctx), {'type': 'text', 'text': PLAIN_OCR_PROMPT}]}]}
        return json.dumps(body).encode(), None

    def _decode(self, ctx, raw, metadata):
        ctx.check()
        try:
            envelope = json.loads(raw)
            choice, = envelope['choices']
            text = choice['message']['content']
            if choice.get('finish_reason') != 'stop' or not isinstance(text, str):
                raise ValueError('Incomplete plain-text result')
        except (ValueError, KeyError, TypeError, AttributeError):
            raise ServiceError('OUTPUT_INVALID', 'OvisOCR3 returned an incomplete or invalid plain-text result', 502) from None
        ctx.metering_tokens = tokens_from_response(envelope)
        return {'text': text}
