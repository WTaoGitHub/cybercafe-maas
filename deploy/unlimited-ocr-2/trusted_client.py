#!/usr/bin/env python3
"""Trusted pilot client for the fixed Unlimited-OCR-2 SGLang protocol."""
import argparse
import base64
import json
import mimetypes
import pathlib
import signal


def collect(lines, chunks):
    done = False
    finishes = []
    for line in lines:
        if not line or not line.startswith('data:'):
            continue
        data = line[5:].strip()
        if data == '[DONE]':
            done = True
            break
        for choice in json.loads(data).get('choices') or []:
            chunks.append((choice.get('delta') or {}).get('content') or '')
            if choice.get('finish_reason') is not None:
                finishes.append(choice['finish_reason'])
    if not done or finishes != ['stop']:
        raise ValueError('stream ended without a successful finish')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--url', required=True, help='trusted engine or gateway base URL')
    parser.add_argument('--model', default='unlimited-ocr-2')
    parser.add_argument('--config', type=pathlib.Path, required=True, help='fixed model config.json')
    parser.add_argument('--output', type=pathlib.Path, required=True, help='private raw output file')
    parser.add_argument('--max-tokens', type=int, required=True, help='whole-document output budget')
    parser.add_argument('images', nargs='+', type=pathlib.Path, help='pages in document order')
    args = parser.parse_args()
    if not 1 <= len(args.images) <= 11 or not 1 <= args.max_tokens <= 20000 * len(args.images):
        parser.error('pilot supports 1–11 pages and a positive bounded output budget')

    import requests
    from sglang.srt.sampling.custom_logit_processor import DeepseekOCRNoRepeatNGramLogitProcessor

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
        'custom_logit_processor': DeepseekOCRNoRepeatNGramLogitProcessor.to_str(),
        'custom_params': {'ngram_size': 35, 'window_size': 1024 if len(args.images) > 1 else 128,
                          'page_token_id': config['sparse_attention_config']['page_token_id'],
                          'eos_token_id': config['eos_token_id'], 'max_page_tokens': 20000},
        'stream': True,
    }
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
                          headers=headers, timeout=(10, 600), stream=True) as response:
            response.raise_for_status()
            collect(response.iter_lines(decode_unicode=True), chunks)
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
