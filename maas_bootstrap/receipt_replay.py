"""Deliver pending GPU finish events from this worker's private local directories."""
from collections import Counter
from http.client import HTTPException
import json
import os
import re
import ssl
import time
from urllib.error import HTTPError
from urllib.request import build_opener, HTTPSHandler, ProxyHandler, Request

from maas_platform.finish_receipt import ATTEMPT, read_finish
from . import execution
from .client import BootstrapError, LocalSettings, LocalState, NoRedirect, ReleaseVerifier, read_file
from .handoff import gpu_uuid


def deliver(directory, name, gpu, key, url, opener):
    payload, original = read_finish(directory, name, gpu, key)
    request = Request(url, data=payload, method='POST', headers={
        'Content-Type': 'application/json', 'Authorization': 'Bearer ' + key})
    try:
        with opener.open(request, timeout=3) as response:
            if response.status != 204:
                return 'unacknowledged'
    except HTTPError as error:
        error.close()
        return 'rejected' if 400 <= error.code < 500 else 'unavailable'
    except (OSError, HTTPException):
        return 'unavailable'
    # A direct callback or a concurrent replay may have acknowledged the same token.
    current = os.stat(name, dir_fd=directory, follow_symlinks=False)
    if os.path.samestat(original, current):
        os.unlink(name, dir_fd=directory)
    return 'acknowledged'


def replay(settings):
    counts = Counter()
    root = execution.local_directory(settings)
    if not (root / 'receipts').is_dir():
        return counts
    state = LocalState(settings.data['state_dir'])
    identity = state.read('identity.json')
    if not identity:
        return counts  # The first bootstrap has not yet committed its GPU identity.
    gpu = gpu_uuid(identity['gpu_uuid'])
    revision = identity['fingerprint']
    if not re.fullmatch('[0-9a-f]{64}', revision):
        raise BootstrapError('LOCAL_IDENTITY_INVALID')
    envelope = json.loads(read_file(state.root / 'generations' / revision / 'config.json', secret=True))
    if ReleaseVerifier().verify(settings, envelope)['fingerprint'] != revision:
        raise BootstrapError('LOCAL_GENERATION_CONFLICT')
    runtime = envelope['manifest'].get('worker_runtime', {})
    folders = {execution.receipt_directory(root, image) for image in runtime.get('images', [])
               if 'receipt_uid' in image}
    if not folders:
        return counts
    key = read_file(root / execution.KEY_NAME, secret=True).decode().strip()
    if not execution.KEY.fullmatch(key):
        raise BootstrapError('EXECUTION_MATERIAL_MISMATCH')
    authority = runtime['execution']
    url = authority['origin'].rstrip('/') + '/maas/internal/pp-ocr-execution'
    context = ssl.create_default_context(cadata=authority['ca_pem'])
    opener = build_opener(ProxyHandler({}), NoRedirect(), HTTPSHandler(context=context))
    for folder in sorted(folders):
        try:
            fd = os.open(folder, os.O_RDONLY | os.O_NOFOLLOW | os.O_DIRECTORY)
        except OSError:
            counts['invalid_or_unreadable'] += 1
            continue
        try:
            with os.scandir(fd) as entries:
                for entry in entries:
                    if not ATTEMPT.fullmatch(entry.name):
                        continue
                    try:
                        outcome = deliver(fd, entry.name, gpu, key, url, opener)
                    except FileNotFoundError:
                        continue
                    except (ValueError, OSError):
                        outcome = 'invalid_or_unreadable'
                    counts[outcome] += 1
                    # One serial sender per worker. Keep a busy or unavailable API bounded.
                    time.sleep(1 if outcome == 'unavailable' else 0.1)
        finally:
            os.close(fd)
    return counts


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', default='/etc/maas/bootstrap.json')
    result = replay(LocalSettings(parser.parse_args().config))
    if result:
        print(json.dumps(result, sort_keys=True), flush=True)
