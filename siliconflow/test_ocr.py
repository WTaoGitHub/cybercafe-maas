#!/usr/bin/env python3
"""Call SiliconFlow OCR with a local image/PDF; credentials stay on disk."""
import argparse
import base64
import json
import mimetypes
from pathlib import Path
import sys
import time
import urllib.error
import urllib.request


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('image', type=Path)
    parser.add_argument('--output', type=Path, default=Path(__file__).parent / 'local-results/response.json')
    args = parser.parse_args()
    key = (Path(__file__).parent / '.mykey').read_text().strip()
    if not key or '\n' in key:
        parser.error('.mykey must contain one API key')
    mime = mimetypes.guess_type(args.image.name)[0]
    if not mime or not (mime.startswith('image/') or mime == 'application/pdf'):
        parser.error('Expected an image or PDF')
    payload = {
        'model': 'deepseek-ai/DeepSeek-OCR', 'stream': False,
        'max_tokens': 2048, 'temperature': 0,
        'messages': [{'role': 'user', 'content': [
            {'type': 'image_url', 'image_url': {'url': 'data:' + mime + ';base64,' + base64.b64encode(args.image.read_bytes()).decode()}},
            {'type': 'text', 'text': '<image>\nFree OCR.'},
        ]}],
    }
    request = urllib.request.Request(
        'https://api.siliconflow.cn/v1/chat/completions',
        data=json.dumps(payload).encode(),
        headers={'Authorization': 'Bearer ' + key, 'Content-Type': 'application/json'},
    )
    started = time.monotonic()
    try:
        with urllib.request.urlopen(request, timeout=180) as response:
            data = json.load(response)
            status = response.status
    except urllib.error.HTTPError as exc:
        print(f'HTTP {exc.code}: ' + exc.read().decode(errors='replace').replace(key, '[REDACTED]'), file=sys.stderr)
        return 1
    except urllib.error.URLError as exc:
        print(f'Connection failed: {exc.reason}', file=sys.stderr)
        return 1
    # Do not persist headers or the request containing image data and credentials.
    safe_data = json.loads(json.dumps(data).replace(key, '[REDACTED]'))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(safe_data, ensure_ascii=False, indent=2) + '\n')
    print(f'HTTP {status}; elapsed {time.monotonic() - started:.2f}s')
    print('Usage:', json.dumps(safe_data.get('usage', {}), ensure_ascii=False))
    choices = safe_data.get('choices', [])
    if not choices or not choices[0].get('message', {}).get('content'):
        print('No OCR text returned', file=sys.stderr)
        return 1
    print('Finish reason:', choices[0].get('finish_reason'))
    print(choices[0]['message']['content'])
    return 0


if __name__ == '__main__':
    sys.exit(main())
