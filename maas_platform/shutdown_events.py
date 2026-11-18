"""Authenticate and record shutdown reports without changing worker state."""
import base64
import asyncio
import hmac
import logging
import re
from datetime import datetime
from pathlib import Path
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StrictInt, field_validator
import psycopg
from fastapi import APIRouter, Request
from starlette.concurrency import run_in_threadpool

from .config import private_text
from .errors import ServiceError

PATH = '/maas/internal/worker-shutdown-events'
PREFIX = b'maas-worker-shutdown-v1\0'
GPU = re.compile(r'GPU-[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}', re.IGNORECASE)
LOG = logging.getLogger(__name__)


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


def record(db, event, scope):
    if any(getattr(event, name) != getattr(scope, name) for name in NodeScope.model_fields):
        raise ServiceError('SHUTDOWN_SCOPE_MISMATCH', 'Event does not match credential scope', 403)
    try:
        with db.transaction() as conn:
            conn.execute("SET LOCAL lock_timeout = '100ms'")
            conn.execute("SET LOCAL statement_timeout = '500ms'")
            return conn.execute('''INSERT INTO worker_shutdown_events
                (event_id,event_type,cluster_id,worker_id,gpu_uuid,boot_id,session_id,occurred_at,received_at,expires_at)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,statement_timestamp(),statement_timestamp()+interval '48 hours')
                RETURNING event_id,received_at,expires_at''',
                (event.event_id, event.event_type, event.cluster_id, event.worker_id, event.gpu_uuid,
                 event.boot_id, event.session_id, event.occurred_at)).fetchone()
    except psycopg.errors.UniqueViolation:
        raise ServiceError('SHUTDOWN_EVENT_EXISTS', 'Event ID already exists', 409) from None
    except (psycopg.Error, OSError):
        raise ServiceError('SHUTDOWN_STORAGE_UNAVAILABLE', 'Event storage unavailable', 503) from None


def cleanup(db, *, now=None):
    """One bounded batch; the optional cutoff is for deterministic private tests."""
    with db.transaction() as conn:
        conn.execute("SET LOCAL lock_timeout = '100ms'")
        conn.execute("SET LOCAL statement_timeout = '500ms'")
        result = conn.execute('''WITH expired AS (
            SELECT event_id FROM worker_shutdown_events
            WHERE expires_at <= coalesce(%s::timestamptz,statement_timestamp())
            ORDER BY expires_at,event_id LIMIT 500 FOR UPDATE SKIP LOCKED
        ) DELETE FROM worker_shutdown_events e USING expired WHERE e.event_id=expired.event_id''', (now,))
        return result.rowcount


async def periodic_cleanup(db, stop):
    while not stop.is_set():
        try:
            await asyncio.to_thread(cleanup, db)
        except Exception:
            LOG.warning('shutdown_event_cleanup_unavailable')
        try:
            await asyncio.wait_for(stop.wait(), timeout=60)
        except TimeoutError:
            pass


def router(db, key_file=None):
    routes = APIRouter(include_in_schema=False)

    @routes.post(PATH, status_code=201)
    async def receive(request: Request):
        scope = authenticate(request.headers.get('authorization', ''), key_file)
        try:
            event = ShutdownEvent.model_validate_json(await request.body())
        except ValueError:
            raise ServiceError('SHUTDOWN_EVENT_INVALID', 'Invalid shutdown event', 422) from None
        result = await run_in_threadpool(record, db, event, scope)
        from fastapi.encoders import jsonable_encoder
        from fastapi.responses import JSONResponse
        return JSONResponse(jsonable_encoder(result), status_code=201, headers={'Cache-Control':'no-store'})

    return routes
