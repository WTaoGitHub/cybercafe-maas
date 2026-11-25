"""Signed execution receipt format shared by the controller and the worker host."""
import hashlib
import hmac
import json
import os
import re
import stat


ATTEMPT = re.compile(r'(task|sync)\.[0-9a-f]{32}')


def read_finish(directory, name, gpu, key):
    if not ATTEMPT.fullmatch(name):
        raise ValueError('Invalid receipt name')
    fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
    with os.fdopen(fd, 'rb') as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode):
            raise ValueError('Invalid receipt file')
        data = stream.read(2049)
    if len(data) > 2048:
        raise ValueError('Oversized receipt')
    signature, payload = data.split(b'\n', 1)
    mac = hmac.new(key.encode(), b'maas-gpu-finish-v1\0' + payload, hashlib.sha256)
    if not hmac.compare_digest(signature, mac.hexdigest().encode()):
        raise ValueError('Invalid receipt signature')
    event = json.loads(payload)
    if (not isinstance(event, dict) or event.get('finished') is not True
            or (event.get('gpu_uuid'), event.get('attempt')) != (gpu, name)
            or not isinstance(event.get('instance'), str) or not re.fullmatch('[0-9a-f]{32}', event['instance'])
            or not isinstance(event.get('route'), str) or not 0 < len(event['route']) <= 128):
        raise ValueError('Invalid finish event')
    return payload, info
