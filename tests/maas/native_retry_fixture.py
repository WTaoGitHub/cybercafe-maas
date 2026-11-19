"""Collect delivery and token digests on disposable native GPUStack targets."""
import argparse
from collections import defaultdict
import hashlib
from threading import Lock
import time

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
import uvicorn

app = FastAPI()
lock = Lock()
events = defaultdict(list)


@app.get('/health')
def health():
    return {'ok': True}


@app.get('/probe/stats')
def stats():
    with lock:
        return dict(events)


@app.post('/v1/chat/completions')
async def chat(body: dict, request: Request):
    label = body['messages'][0]['content']
    if not isinstance(label, str) or not label.startswith('test_agent_retry_') or len(label) > 96:
        raise HTTPException(400, 'Only marked synthetic requests are accepted')
    headers = request.headers.getlist('X-MaaS-Execution-Attempt')
    digests = [hashlib.sha256(token.encode()).hexdigest() for token in headers]
    with lock:
        if sum(map(len, events.values())) >= 200:
            raise HTTPException(429, 'Probe storage ceiling reached')
        events[label].append(digests)
    if label.endswith('_503'):
        return JSONResponse({'error': {'message': 'Synthetic retry response', 'type': 'server_error'}}, 503)
    return {'id': 'test-agent-retry', 'object': 'chat.completion', 'created': int(time.time()),
            'model': body['model'], 'choices': [{'index': 0, 'finish_reason': 'stop',
                'message': {'role': 'assistant', 'content': 'Synthetic retry probe'}}],
            'usage': {'prompt_tokens': 0, 'completion_tokens': 0, 'total_tokens': 0}}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port', required=True, type=int)
    args = parser.parse_args()
    uvicorn.run(app, host='0.0.0.0', port=args.port, access_log=False)
