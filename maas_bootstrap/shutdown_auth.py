"""Issue narrowly scoped event credentials; never reuse registration secrets."""
import base64
import hmac
import json
import re
from uuid import UUID

from .client import BootstrapError, canonical, read_file
from .handoff import gpu_uuid, positive_id

PREFIX = b'maas-worker-shutdown-v1\0'


def issue(key_file, binding):
    if set(binding) != {'cluster_id', 'worker_id', 'gpu_uuid', 'boot_id', 'session_id'}:
        raise BootstrapError('SHUTDOWN_EVENT_SCOPE_INVALID')
    try:
        if not all(positive_id(binding[k]) for k in ('cluster_id', 'worker_id')):
            raise ValueError()
        scope = dict(binding, gpu_uuid=gpu_uuid(binding['gpu_uuid']),
                     boot_id=str(UUID(binding['boot_id'])), session_id=str(UUID(binding['session_id'])))
        raw = read_file(key_file, secret=True).decode().strip()
        if not re.fullmatch('[0-9a-f]{64}', raw):
            raise ValueError()
    except (ValueError, TypeError, AttributeError, UnicodeError):
        raise BootstrapError('SHUTDOWN_EVENT_CREDENTIAL_INVALID') from None
    payload = canonical(scope)
    signature = hmac.digest(bytes.fromhex(raw), PREFIX + payload, 'sha256')
    encode = lambda value: base64.urlsafe_b64encode(value).rstrip(b'=').decode()
    return 'mse1.' + encode(payload) + '.' + encode(signature)


def main():
    """Private operator delivery from an already verified binding; no native writes."""
    import argparse
    from pathlib import Path
    from .client import atomic_bytes
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--key-file', required=True)
    p.add_argument('--binding-file', required=True)
    p.add_argument('--output', required=True)
    a = p.parse_args()
    try:
        token = issue(a.key_file, json.loads(read_file(a.binding_file, secret=True)))
        output = Path(a.output)
        if output.exists() or output.is_symlink():
            raise BootstrapError('SHUTDOWN_EVENT_OUTPUT_EXISTS')
        atomic_bytes(output, token.encode() + b'\n')
        print('{"code":"SHUTDOWN_EVENT_CREDENTIAL_WRITTEN"}')
        return 0
    except Exception:
        print('{"code":"SHUTDOWN_EVENT_CREDENTIAL_UNAVAILABLE"}')
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
