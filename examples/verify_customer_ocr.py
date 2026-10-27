#!/usr/bin/env python3
"""Verify a customer key, optionally sending one OCR request. Python 3, no dependencies.

First establish the authorized SSH tunnel to 001, for example:
  ssh -N -o ExitOnForwardFailure=yes -p 2023 -i ~/.ssh/id_ed25519 \
      -L 127.0.0.1:18832:127.0.0.1:18832 root@111.4.255.126

Permission check only:
  python3 examples/verify_customer_ocr.py
Permission check followed by one metered OCR request:
  python3 examples/verify_customer_ocr.py --image /absolute/path/sample.png

Do not put a secret key in command arguments. Use --key-file for another key file.
"""

import argparse
import base64
import json
from pathlib import Path
import ssl
import stat
import sys
import urllib.error
import urllib.parse
import urllib.request


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def main(default_base_url='https://127.0.0.1:18832', allow_local_http=False,
         description=None):
    root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description=description or __doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--base-url', default=default_base_url)
    parser.add_argument('--key-file', type=Path,
                        default=Path.home() / '.maas-credentials/customer-a-key')
    parser.add_argument('--cert-file', type=Path,
                        default=root / '.maas/maas-server-current-20261027.crt')
    parser.add_argument('--image', type=Path, help='PNG/JPEG; omit to check permissions only')
    args = parser.parse_args()
    base = args.base_url.rstrip('/')
    url = urllib.parse.urlsplit(base)
    local_http = (allow_local_http and url.scheme == 'http'
                  and url.hostname in ('127.0.0.1', '::1'))
    if ((url.scheme != 'https' and not local_http) or not url.hostname or url.username is not None
            or url.password is not None or url.query or url.fragment or url.path):
        parser.error('--base-url must be HTTPS (HTTP example also permits loopback HTTP), '
                     'without path, credentials or query')

    try:
        key_path = args.key_file.expanduser()
        if stat.S_IMODE(key_path.stat().st_mode) & 0o077:
            raise ValueError('Key file must be private: chmod 600 <key-file>')
        key = key_path.read_text().strip()
        if not key or len(key) > 1024 or any(ord(c) < 33 or ord(c) > 126 for c in key):
            raise ValueError('Key file must contain only the API key, without Bearer prefix')
        if key.startswith('Bearer '):
            raise ValueError('Remove the Bearer prefix from the key file')
        context = (ssl.create_default_context(cafile=str(args.cert_file.expanduser()))
                   if url.scheme == 'https' else None)
        raw = args.image.expanduser().read_bytes() if args.image else None
        if raw is not None and not 0 < len(raw) <= 10 * 1024 * 1024:
            raise ValueError('Image must be nonempty and at most 10 MiB')
    except (OSError, ValueError) as exc:
        print('Local file/configuration error:', exc, file=sys.stderr)
        return 1

    # No proxy environment, redirects or retry loop. HTTPS always verifies its certificate.
    opener = urllib.request.build_opener(
        urllib.request.ProxyHandler({}), NoRedirect(),
        urllib.request.HTTPSHandler(context=context))

    def call(path, body=None):
        headers = {'Authorization': 'Bearer ' + key, 'Accept': 'application/json'}
        data = None
        if body is not None:
            data = json.dumps(body).encode()
            headers['Content-Type'] = 'application/json'
        request = urllib.request.Request(base + path, data=data, headers=headers)
        with opener.open(request, timeout=45) as response:
            print('HTTP:', response.status,
                  'X-Request-ID:', response.headers.get('X-Request-ID', '(absent)'))
            return json.load(response)

    inference_started = False
    try:
        print('Checking customer key and ocr.default permission (no OCR execution)...')
        capability = call('/maas/v1/models/ocr.default/capabilities')
        if capability.get('model') != 'ocr.default':
            raise ValueError('Unexpected capability response')
        print('Permission check passed. Revision:', capability.get('revision'))
        if raw is None:
            print('No image supplied; no OCR request or successful-inference metering performed.')
            return 0
        print('Sending ONE OCR request; success creates one usage event...')
        inference_started = True
        result = call('/ocr', {
            'model': 'ocr.default',
            'image_base64': base64.b64encode(raw).decode('ascii'),
            'return_boxes': True,
        })
        if not isinstance(result.get('lines'), list):
            raise ValueError('Unexpected OCR response: missing lines array')
        print(json.dumps(result, ensure_ascii=False, indent=2))
        print('OCR passed. Lines:', len(result['lines']), '(empty is also a valid success)')
        return 0
    except urllib.error.HTTPError as exc:
        print('HTTP error:', exc.code, 'X-Request-ID:',
              exc.headers.get('X-Request-ID', '(absent)'), file=sys.stderr)
        try:
            error = json.loads(exc.read(65536)).get('error', {})
            print('Error code:', error.get('code', '(absent)'), file=sys.stderr)
        except (ValueError, AttributeError):
            print('Non-JSON gateway response.', file=sys.stderr)
    except (urllib.error.URLError, OSError, ValueError) as exc:
        print('Connection, TLS or response error:', exc, file=sys.stderr)
        print('Check the SSH tunnel and address; for HTTPS also check the certificate.',
              file=sys.stderr)
    if inference_started:
        print('No automatic retry. If completion is uncertain, retain the request ID/time '
              'and contact operations before sending again.', file=sys.stderr)
    return 1


if __name__ == '__main__':
    sys.exit(main())
