"""GPU-only OCR probe with OpenAI-compatible transport for GPUStack."""
import argparse
import base64
import io
import os
import subprocess
import threading
import time
import uuid

import numpy as np
from fastapi import FastAPI, HTTPException
from fastapi.responses import StreamingResponse
from ocr_protocol import completion_events
from PIL import Image
from rapidocr_onnxruntime import RapidOCR
import uvicorn

parser = argparse.ArgumentParser()
parser.add_argument('--port', type=int, default=18820)
parser.add_argument('--host', default='0.0.0.0')
parser.add_argument('--model', default='rapidocr-gpu')
args = parser.parse_args()
node_name = os.getenv('NODE_NAME') or subprocess.check_output(
    ['nvidia-smi', '--query-gpu=uuid', '--format=csv,noheader'], text=True).strip()
engine = RapidOCR(det_use_cuda=True, cls_use_cuda=True, rec_use_cuda=True,
                  intra_op_num_threads=2, inter_op_num_threads=2)
providers = {}
for name in ('text_det', 'text_cls', 'text_rec'):
    component = getattr(engine, name)
    wrapper = component.infer if hasattr(component, 'infer') else component.session
    session = wrapper.session
    providers[name] = session.get_providers()
if any('CUDAExecutionProvider' not in p for p in providers.values()):
    raise RuntimeError(f'GPU OCR cannot silently fall back to CPU: {providers}')
app = FastAPI()
lock = threading.Lock()

@app.get('/health')
def health():
    return {'ok': True, 'node': node_name, 'providers': providers}

@app.get('/v1/models')
def models():
    return {'object': 'list', 'data': [{'id': args.model, 'object': 'model', 'owned_by': 'cybercafe'}]}

def recognize(encoded):
    try:
        raw = base64.b64decode(encoded.split(',', 1)[-1], validate=True)
        if len(raw) > 10 * 1024 * 1024:
            raise ValueError('image exceeds 10MB')
        image = Image.open(io.BytesIO(raw))
        if image.width * image.height > 20_000_000:
            raise ValueError('image exceeds 20 megapixels')
        array = np.asarray(image.convert('RGB'))[:, :, ::-1].copy()
    except Exception as exc:
        raise HTTPException(400, str(exc))
    start = time.perf_counter()
    with lock:
        result, _ = engine(array)
    lines = [{'box': box, 'text': text, 'score': float(score)} for box, text, score in (result or [])]
    return {'text': '\n'.join(x['text'] for x in lines), 'lines': lines,
            'elapsed_ms': round((time.perf_counter()-start)*1000, 2),
            'node': node_name}

@app.post('/ocr')
def ocr(body: dict):
    return recognize(body.get('image_base64', ''))

@app.post('/v1/chat/completions')
def chat(body: dict):
    encoded = None
    for message in body.get('messages', []):
        content = message.get('content')
        if isinstance(content, list):
            for part in content:
                if part.get('type') == 'image_url':
                    encoded = part.get('image_url', {}).get('url', '')
    if not encoded or not encoded.startswith('data:image/'):
        raise HTTPException(400, 'supply a base64 image data URL')
    import json
    result = recognize(encoded)
    completion_id = 'ocr-'+uuid.uuid4().hex
    created = int(time.time())
    model = body.get('model', args.model)
    if body.get('stream'):
        return StreamingResponse(completion_events(result, completion_id, created, model),
                                 media_type='text/event-stream',
                                 headers={'Cache-Control': 'no-cache', 'X-Accel-Buffering': 'no'})
    return {'id': completion_id, 'object': 'chat.completion', 'created': created,
            'model': model,
            'choices': [{'index': 0, 'message': {'role': 'assistant', 'content': json.dumps(result, ensure_ascii=False)}, 'finish_reason': 'stop'}]}

uvicorn.run(app, host=args.host, port=args.port)
