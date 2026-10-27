import base64
import json
import math
from ..core import case, check
from ..fixtures import blank_png, oversized_pixel_png


def lines(value):
    check(isinstance(value, dict) and isinstance(value.get('lines'), list), 'Missing lines array')
    for row in value['lines']:
        check(isinstance(row.get('text'), str), 'Invalid line text')
        if 'confidence' in row:
            n = row['confidence']
            check(isinstance(n, (int, float)) and not isinstance(n, bool)
                  and math.isfinite(n) and 0 <= n <= 1, 'Invalid confidence')
        if 'polygon' in row:
            box = row['polygon']
            check(isinstance(box, list) and len(box) == 4, 'Invalid polygon')
            for point in box:
                check(isinstance(point, list) and len(point) == 2
                      and all(isinstance(n, (int, float)) and not isinstance(n, bool)
                              and math.isfinite(n) for n in point), 'Invalid polygon point')
    return value['lines']


@case('OCR-001', 'ocr', 'Bare base64 through real OCR', ('positive', 'gpu', 'smoke'), ('inference',))
def bare(c):
    r = c.request('POST', '/ocr', body=c.ocr_body(request_id=c.run_id), inference=True).expect(200)
    rows = lines(r.json())
    check('choices' not in r.json(), 'Facade must not return Chat envelope')
    check(r.headers.get('cache-control') == 'no-store', 'Missing no-store response')
    c.current['observations'] = {'line_count': len(rows)}


@case('OCR-002', 'ocr', 'Data URL via v1 alias with polygons omitted', ('positive', 'gpu', 'contract'), ('inference',))
def alias(c):
    raw = c.image()
    media = 'image/jpeg' if raw.startswith(b'\xff\xd8') else 'image/png'
    body = {'model': c.model, 'image_base64': 'data:' + media + ';base64,' + base64.b64encode(raw).decode(),
            'return_boxes': False, 'language': 'auto'}
    rows = lines(c.request('POST', '/v1/ocr', body=body, inference=True).expect(200).json())
    check(all('polygon' not in row for row in rows), 'return_boxes=false was ignored')


@case('OCR-003', 'ocr', 'Blank PNG yields successful empty lines', ('positive', 'gpu', 'fixture'), ('inference',))
def blank(c):
    body = {'model': c.model, 'image_base64': base64.b64encode(blank_png()).decode()}
    check(c.request('POST', '/ocr', body=body, inference=True).expect(200).json() == {'lines': []},
          'Blank fixture should have no recognized lines')


@case('OCR-004', 'ocr', 'Real JPEG fixture accepted', ('positive', 'gpu', 'optional'),
      ('inference',), ('jpeg_file',))
def jpeg(c):
    body = {'model': c.model, 'image_base64': base64.b64encode(c.image('jpeg_file')).decode()}
    lines(c.request('POST', '/ocr', body=body, inference=True).expect(200).json())


@case('OCR-005', 'ocr', 'Public image URL downloaded and inferred', ('positive', 'gpu', 'external', 'optional'),
      ('inference',), ('public_image_url',))
def public_url(c):
    lines(c.request('POST', '/ocr', body={'model': c.model, 'image_url': c.config['public_image_url']},
                    inference=True).expect(200).json())


@case('OCR-006', 'ocr', 'Configured bilingual expected text is recognized', ('accuracy', 'gpu', 'optional'),
      ('inference',), ('image_file', 'expected_text_contains'))
def accuracy(c):
    rows = lines(c.request('POST', '/ocr', body=c.ocr_body(), inference=True).expect(200).json())
    text = '\n'.join(row['text'] for row in rows)
    check(all(piece in text for piece in c.config['expected_text_contains']),
          'Expected fixture text was not found (recognized text excluded from report)')


def negative(ident, title, patch, code, status=422):
    @case(ident, 'ocr_validation', title, ('negative', 'contract'), ('write',))
    def run(c):
        body = c.ocr_body()
        patch(body)
        c.request('POST', '/ocr', body=body).expect(status, code)


negative('OCR-V001', 'Missing image source', lambda b: b.pop('image_base64'), 'INPUT_INVALID')
negative('OCR-V002', 'Two image sources rejected', lambda b: b.update(image_url='https://example.org/a.png'), 'INPUT_INVALID')
negative('OCR-V003', 'Malformed base64 rejected', lambda b: b.update(image_base64='invalid!'), 'IMAGE_BASE64')
negative('OCR-V004', 'Unsupported language hint rejected', lambda b: b.update(language='unsupported'), 'INPUT_INVALID')
negative('OCR-V005', 'Boolean must not be string', lambda b: b.update(return_boxes='false'), 'INPUT_INVALID')
negative('OCR-V006', 'Unknown request field rejected', lambda b: b.update(provider='openai'), 'INPUT_INVALID')
negative('OCR-V007', 'Request ID control character rejected', lambda b: b.update(request_id='bad\nvalue'), 'INPUT_INVALID')
negative('OCR-V008', 'Non-image bytes rejected', lambda b: b.update(image_base64=base64.b64encode(b'not an image').decode()), 'IMAGE_INVALID')
negative('OCR-V009', 'Data URL media mismatch rejected', lambda b: b.update(image_base64='data:image/jpeg;base64,'+base64.b64encode(blank_png()).decode()), 'IMAGE_FORMAT', 415)
negative('OCR-V010', 'PNG/JPEG-only data URL format', lambda b: b.update(image_base64='data:image/gif;base64,R0lGODlh'), 'IMAGE_FORMAT', 415)
negative('OCR-V011', 'Oversized pixel dimensions rejected without GPU', lambda b: b.update(image_base64=base64.b64encode(oversized_pixel_png()).decode()), 'IMAGE_PIXELS', 413)
negative('OCR-V012', 'Unknown logical model is not aliased', lambda b: b.update(model='e2e-absent-model'), 'MODEL_NOT_FOUND', 404)


@case('OCR-V013', 'ocr_validation', 'Private/loopback URLs, ports and credentials rejected',
      ('negative', 'ssrf', 'boundary'), ('write',))
def prohibited(c):
    for url in ('http://127.0.0.1/a.png', 'http://169.254.169.254/latest/meta-data/',
                'http://10.11.31.1/a.png', 'https://example.org:8443/a.png',
                'https://user:password@example.org/a.png'):
        c.request('POST', '/ocr', body={'model': c.model, 'image_url': url}).expect(422, 'IMAGE_URL_NOT_ALLOWED')


@case('OCR-V014', 'ocr_validation', 'Malformed JSON safely rejected', ('negative', 'contract'), ('write',))
def bad_json(c):
    c.request('POST', '/ocr', raw=b'{').expect(422, 'INPUT_INVALID')


@case('CHAT-001', 'chat', 'Legacy Chat envelope and nested OCR JSON', ('positive', 'gpu', 'legacy'), ('inference',))
def chat(c):
    raw = c.image()
    media = 'image/jpeg' if raw.startswith(b'\xff\xd8') else 'image/png'
    body = {'model': c.model, 'stream': False, 'messages': [{'role': 'user', 'content': [
        {'type': 'image_url', 'image_url': {'url': 'data:'+media+';base64,'+base64.b64encode(raw).decode()}}]}]}
    value = c.request('POST', '/v1/chat/completions', body=body, inference=True).expect(200).json()
    check(value['object'] == 'chat.completion', 'Missing Chat completion envelope')
    output = json.loads(value['choices'][0]['message']['content'])
    check(isinstance(output['text'], str) and isinstance(output['lines'], list), 'Invalid legacy OCR output')


@case('CHAT-002', 'chat', 'Streaming not supported on MaaS Chat', ('negative', 'legacy'), ('write',))
def stream(c):
    c.request('POST', '/v1/chat/completions', body={'model': c.model, 'stream': True, 'messages': []}).expect(422, 'SYNC_INPUT')
