"""Authenticate and record shutdown reports without changing worker state."""
import base64
import hmac
import json
import re
from datetime import datetime
from pathlib import Path
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StrictInt, field_validator

from .config import private_text
from .errors import ServiceError

PATH = '/maas/internal/worker-shutdown-events'
PREFIX = b'maas-worker-shutdown-v1\0'
GPU = re.compile(r'GPU-[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}', re.IGNORECASE)


class NodeScope(BaseModel):
    model_config = ConfigDict(extra='forbid')
    cluster_id: Annotated[StrictInt, Field(gt=0, le=2147483647)]
    worker_id: Annotated[StrictInt, Field(gt=0, le=9223372036854775807)]
    gpu_uuid: str
    boot_id: UUID
    session_id: UUID

    @field_validator('gpu_uuid')
    @classmethod
    def gpu(cls, value):
        if not GPU.fullmatch(value):
            raise ValueError('Invalid GPU UUID')
        return 'GPU-' + value[4:].lower()

    @field_validator('boot_id', 'session_id', mode='before')
    @classmethod
    def uuid_text(cls, value):
        if not isinstance(value, (str, UUID)):
            raise ValueError('UUID text required')
        return value


class ShutdownEvent(NodeScope):
    event_id: UUID
    event_type: Literal['worker.shutdown.requested']
    occurred_at: datetime

    @field_validator('event_id', mode='before')
    @classmethod
    def event_uuid(cls, value):
        if not isinstance(value, (str, UUID)) or UUID(str(value)).version != 4:
            raise ValueError('UUID v4 required')
        return value

    @field_validator('occurred_at', mode='before')
    @classmethod
    def timestamp(cls, value):
        if not isinstance(value, str) or not re.fullmatch(
                r'\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?(?:Z|[+-]\d{2}:\d{2})', value):
            raise ValueError('RFC 3339 text required')
        parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
        if parsed.utcoffset() is None:
            raise ValueError('Timezone required')
        return parsed


def authenticate(authorization, key_file):
    if key_file is None:
        raise ServiceError('SHUTDOWN_EVENTS_DISABLED', 'Not found', 404)
    try:
        if Path(key_file).is_symlink():
            raise ValueError()
        raw = private_text(key_file)
        if not re.fullmatch('[0-9a-f]{64}', raw):
            raise ValueError()
        key = bytes.fromhex(raw)
    except (OSError, ValueError):
        raise ServiceError('SHUTDOWN_AUTH_UNAVAILABLE', 'Event authentication unavailable', 503) from None
    try:
        if (not isinstance(authorization, str) or not authorization.startswith('Bearer ')
                or len(authorization[7:]) > 1024):
            raise ValueError()
        version, encoded, signature = authorization[7:].split('.')
        if version != 'mse1' or any(not re.fullmatch('[A-Za-z0-9_-]+', part) for part in (encoded, signature)):
            raise ValueError()
        payload = base64.b64decode(encoded + '=' * (-len(encoded) % 4), altchars=b'-_', validate=True)
        actual = base64.b64decode(signature + '=' * (-len(signature) % 4), altchars=b'-_', validate=True)
        if not hmac.compare_digest(actual, hmac.digest(key, PREFIX + payload, 'sha256')):
            raise ValueError()
        return NodeScope.model_validate_json(payload)
    except (ValueError, TypeError):
        raise ServiceError('SHUTDOWN_UNAUTHENTICATED', 'Invalid node event credential', 401) from None
