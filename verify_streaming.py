"""Real GPUStack gateway checks with the same stream flags as Playground."""
import base64
import argparse
import json
from pathlib import Path
import urllib.error
import urllib.request
parser = argparse.ArgumentParser()
parser.add_argument('--direct-base', help='test a private probe without GPUStack authentication')
args = parser.parse_args()
if args.direct_base:
    base = args.direct_base.rstrip('/')
    opener = urllib.request.build_opener()
else:
    from gpustack_admin import base, opener

encoded = base64.b64encode(Path('/root/wtaobj/manual-fixtures/rctw17_image_1941.jpg').read_bytes()).decode()
body = {'model': 'rapidocr-gpu', 'stream': True, 'stream_options': {'include_usage': True},
        'messages': [{'role': 'user', 'content': [
            {'type': 'text', 'text': '请提取图片里的文字'},
            {'type': 'image_url', 'image_url': {'url': 'data:image/jpeg;base64,'+encoded}}]}]}

def call(payload):
    return opener.open(urllib.request.Request(base+'/v1/chat/completions',
        data=json.dumps(payload).encode(), headers={'Content-Type': 'application/json'}), timeout=60)

nodes = {}
example = None
for _ in range(12):
    with call(body) as response:
        assert response.headers.get_content_type() == 'text/event-stream'
        events = [line[6:] for line in response.read().decode().splitlines() if line.startswith('data: ')]
    assert events[-1] == '[DONE]', events
    chunks = [json.loads(e) for e in events[:-1]]
    assert chunks[0]['choices'][0]['delta']['role'] == 'assistant'
    assert chunks[-1]['choices'][0]['finish_reason'] == 'stop'
    assert len({c['id'] for c in chunks}) == 1
    text = ''.join(c['choices'][0]['delta'].get('content','') for c in chunks)
    assert text and any('\u4e00' <= ch <= '\u9fff' for ch in text)
    node = chunks[-1]['ocr_metadata']['node']
    nodes[node] = nodes.get(node, 0)+1
    example = text
assert len(nodes) == (1 if args.direct_base else 2), nodes
with call({**body, 'stream': False}) as response:
    assert response.headers.get_content_type() == 'application/json'
    result = json.loads(json.load(response)['choices'][0]['message']['content'])
assert result['text'] and isinstance(result['lines'], list)
try:
    call({**body, 'messages': [{'role':'user','content':'no image'}]})
    raise AssertionError('missing image must fail')
except urllib.error.HTTPError as exc:
    assert exc.code == 400, exc.code
report = {'ok': True, 'stream_requests': 12, 'nodes': nodes,
          'nonstream_json_preserved': True, 'missing_image_http_400': True,
          'stream_text_example': example, 'usage': 'not fabricated; OCR has no token usage'}
filename = 'streaming-direct-verification.json' if args.direct_base else 'streaming-verification.json'
Path('/root/wtaobj', filename).write_text(json.dumps(report, ensure_ascii=False, indent=2))
print(json.dumps(report, ensure_ascii=False))
