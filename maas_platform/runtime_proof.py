"""Wire format shared by the MaaS authority and the PP host evidence reader."""
import hashlib
import hmac
import json
import re
import uuid

GPU = re.compile(r'GPU-[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}')
HEX = re.compile(r'[0-9a-f]{64}')
INSTANCE = re.compile(r'[0-9a-f]{32}')
AUTHORITY_PATH = '/maas/internal/pp-ocr-execution'


def signature(key, purpose, payload):
    data = json.dumps(payload, sort_keys=True, separators=(',', ':')).encode()
    return hmac.new(key.encode(), b'maas-pp-runtime-v1\0' + purpose.encode() + b'\0' + data,
                    hashlib.sha256).hexdigest()


def cgroup_paths(container):
    if not isinstance(container, str) or not HEX.fullmatch(container):
        raise ValueError('Invalid full Docker container ID')
    return (f'/system.slice/docker-{container}.scope', f'/docker/{container}')


def valid_identity(value):
    if not isinstance(value, dict) or set(value) != {'boot', 'container', 'cgroup', 'inode', 'started', 'pid'}:
        return False
    try:
        if str(uuid.UUID(value['boot'])) != value['boot']:
            return False
        if value['cgroup'] not in cgroup_paths(value['container']):
            return False
    except (ValueError, AttributeError, TypeError):
        return False
    return (type(value['inode']) is int and 0 < value['inode'] < 2**63 and
            type(value['pid']) is int and 0 < value['pid'] < 2**31 and
            isinstance(value['started'], str) and
            re.fullmatch(r'\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(?:\.\d{1,9})?Z', value['started']) is not None and
            not value['started'].startswith('0001-'))
