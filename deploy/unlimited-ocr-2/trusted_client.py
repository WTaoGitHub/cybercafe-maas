#!/usr/bin/env python3
"""Trusted pilot client for the fixed Unlimited-OCR-2 SGLang protocol."""
import argparse
import base64
import json
import mimetypes
import pathlib
import signal


def collect(response, chunks):
    choices = response.get('choices') or []
    if len(choices) != 1 or choices[0].get('finish_reason') != 'stop':
        raise ValueError('document ended without a successful finish')
    text = (choices[0].get('message') or {}).get('content')
    if not isinstance(text, str) or not text:
        raise ValueError('document returned no text')
    chunks.append(text)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--url', help='trusted engine or gateway base URL')
    parser.add_argument('--model', default='unlimited-ocr-2')
    parser.add_argument('--config', type=pathlib.Path, required=True, help='fixed model config.json')
    parser.add_argument('--output', type=pathlib.Path, help='private raw output file')
    parser.add_argument('--request-json', type=pathlib.Path, help='write the request for curl without sending it')
    parser.add_argument('--max-tokens', type=int, required=True, help='whole-document output budget')
    parser.add_argument('images', nargs='+', type=pathlib.Path, help='pages in document order')
    args = parser.parse_args()
    if not 1 <= len(args.images) <= 11 or not 1 <= args.max_tokens <= 20000 * len(args.images):
        parser.error('pilot supports 1–11 pages and a positive bounded output budget')
    if not args.request_json and (not args.url or not args.output):
        parser.error('--url and --output are required when sending a request')

    config = json.loads(args.config.read_text())
    content = []
    for path in args.images:
        mime = mimetypes.guess_type(path.name)[0]
        if mime not in ('image/png', 'image/jpeg'):
            parser.error('only PNG and JPEG pages are supported')
        image = base64.b64encode(path.read_bytes()).decode('ascii')
        content.append({'type': 'image_url', 'image_url': {'url': f'data:{mime};base64,{image}'}})
    content.append({'type': 'text', 'text': '<PAGE><image>' * len(args.images) + 'Multi page parsing.'})
    payload = {
        'model': args.model, 'messages': [{'role': 'user', 'content': content}],
        'max_tokens': args.max_tokens, 'temperature': 0, 'skip_special_tokens': False,
        'images_config': {'crop_mode': True},
        'custom_logit_processor': 'maas.unlimited-ocr-2.ngram.v1',
        'custom_params': {'ngram_size': 35, 'window_size': 1024 if len(args.images) > 1 else 128,
                          'page_token_id': config['sparse_attention_config']['page_token_id'],
                          'eos_token_id': config['eos_token_id'], 'max_page_tokens': 20000},
        'stream': False,
    }
    if args.request_json:
        args.request_json.write_text(json.dumps(payload))
        return

    import requests
    import os
    key = os.environ.get('UOCR_API_KEY')
    headers = {'Authorization': 'Bearer ' + key} if key else {}
    session = requests.Session()
    session.trust_env = False
    chunks = []
    def deadline(_signum, _frame):
        raise TimeoutError('document deadline exceeded; remote execution may still be active')
    signal.signal(signal.SIGALRM, deadline)
    signal.alarm(900)
    try:
        with session.post(args.url.rstrip('/') + '/v1/chat/completions', json=payload,
                          headers=headers, timeout=(10, 600),
                          allow_redirects=False) as response:
            response.raise_for_status()
            collect(response.json(), chunks)
        text = ''.join(chunks)
        if not text or text.count('<PAGE>') != len(args.images):
            raise ValueError('output page count does not match the document')
        print(json.dumps({'pages': len(args.images), 'characters': len(text), 'finish_reason': 'stop'}))
    finally:
        signal.alarm(0)
        args.output.write_text(''.join(chunks))
        session.close()


if __name__ == '__main__':
    main()
