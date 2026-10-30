#!/usr/bin/env python3
"""Bounded service probes. Never retries failed inference requests."""
import base64
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
import json
import mimetypes
from pathlib import Path
import time
import urllib.error
import urllib.request

ROOT = Path(__file__).resolve().parent
OUT = ROOT / 'local-results/service-benchmark'
MODEL = 'deepseek-ai/DeepSeek-OCR'
PROMPT = '<image>\nFree OCR.'


def call(image, max_tokens=2048):
    key = (ROOT / '.mykey').read_text().strip()
    payload = {'model': MODEL, 'stream': False,
               'temperature': 0, 'max_tokens': max_tokens,
               'messages': [{'role': 'user', 'content': [
                   {'type': 'image_url', 'image_url': {'url': 'data:' + (mimetypes.guess_type(image.name)[0] or 'application/octet-stream') + ';base64,' + base64.b64encode(image.read_bytes()).decode()}},
                   {'type': 'text', 'text': PROMPT}]}]}
    body = json.dumps(payload).encode()
    start = time.monotonic()
    record = {'model': MODEL, 'prompt': PROMPT, 'image': image.name, 'request_bytes': len(body), 'max_tokens': max_tokens}
    req = urllib.request.Request('https://api.siliconflow.cn/v1/chat/completions', data=body,
                                 headers={'Authorization': 'Bearer ' + key, 'Content-Type': 'application/json'})
    try:
        with urllib.request.urlopen(req, timeout=60) as response:
            data = json.load(response)
            record.update(status=response.status, usage=data.get('usage'),
                          finish_reason=data.get('choices', [{}])[0].get('finish_reason'),
                          text=data.get('choices', [{}])[0].get('message', {}).get('content', ''))
            record['limits'] = {k: v for k, v in response.headers.items() if 'ratelimit' in k.lower() or 'retry-after' in k.lower()}
    except urllib.error.HTTPError as exc:
        record.update(status=exc.code, error=exc.read().decode(errors='replace').replace(key, '[REDACTED]'))
        record['limits'] = {k: v for k, v in exc.headers.items() if 'ratelimit' in k.lower() or 'retry-after' in k.lower()}
    except Exception as exc:
        record.update(status=0, error=str(exc).replace(key, '[REDACTED]'))
    record['elapsed_s'] = time.monotonic() - start
    return record


def run_group(label, image, concurrency, count, max_tokens=2048):
    start = time.monotonic()
    with ThreadPoolExecutor(max_workers=concurrency) as pool:
        records = list(pool.map(lambda _: call(image, max_tokens), range(count)))
    elapsed = time.monotonic() - start
    good = [r for r in records if r['status'] == 200 and r.get('text')]
    times = sorted(r['elapsed_s'] for r in good)
    summary = {'label': label, 'concurrency': concurrency, 'count': count,
               'elapsed_s': elapsed, 'statuses': dict(Counter(r['status'] for r in records)),
               'success_rps': len(good) / elapsed,
               'p50_s': times[(len(times)-1)//2] if times else None,
               'p95_s': times[min(len(times)-1, int(len(times)*.95))] if times else None}
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / (label + '.json')).write_text(json.dumps({'summary': summary, 'records': records}, ensure_ascii=False, indent=2))
    print(json.dumps(summary), flush=True)
    for r in records:
        if r['status'] != 200:
            print('FIRST ERROR:', r.get('error'), flush=True)
            break
    return records


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--model', default=MODEL)
    parser.add_argument('--prompt', default=PROMPT)
    parser.add_argument('--label', default='ramp')
    args = parser.parse_args()
    if not args.label or any(c not in 'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_' for c in args.label):
        parser.error('--label must contain only letters, digits, hyphens or underscores')
    MODEL, PROMPT = args.model, args.prompt
    image = ROOT / 'local-results/smoke-test.png'
    for c in (1, 2, 4, 8, 16, 32):
        records = run_group(args.label + '-c' + str(c), image, c, max(4, c * 2))
        if any(r['status'] != 200 for r in records):
            print('STOP: error observed; no higher concurrency and no retries', flush=True)
            break
