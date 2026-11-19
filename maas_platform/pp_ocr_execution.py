"""Worker-authenticated start and finish for the guarded PP-OCRv4 route."""
import hmac
import json
import re
from typing import Annotated

from fastapi import APIRouter, Header, Response
from pydantic import BaseModel, StringConstraints

from .errors import ServiceError

PATH = '/maas/internal/pp-ocr-execution'
ATTEMPT_HEADER = 'X-MaaS-Execution-Attempt'
TABLE = {'task': 'backend_activity', 'sync': 'sync_activity'}
HexID = Annotated[str, StringConstraints(pattern=r'^[a-f0-9]{32}$')]


class GPUEvent(BaseModel):
    attempt: Annotated[str, StringConstraints(pattern=r'^(task|sync)\.[0-9a-f]{32}$')]
    gpu_uuid: Annotated[str, StringConstraints(pattern=r'^GPU-[0-9a-f]{8}(-[0-9a-f]{4}){3}-[0-9a-f]{12}$')]
    instance: HexID
    route: Annotated[str, StringConstraints(min_length=1, max_length=128)]
    finished: bool


def finished(db, kind, token):
    with db.transaction() as conn:
        return bool(conn.execute(f'SELECT 1 FROM {TABLE[kind]} WHERE token=%s AND gpu_finished_at IS NOT NULL',
                                 (token,)).fetchone())


def router(db, settings):
    routes = APIRouter()

    @routes.post(PATH, include_in_schema=False, status_code=204)
    def event(body: GPUEvent, authorization: str = Header(default='')):
        from .config import private_text

        if settings.pp_ocr_worker_keys_file is None:
            raise ServiceError('EXECUTION_GUARD_DISABLED', 'Execution guard is disabled', 404)
        keys = json.loads(private_text(settings.pp_ocr_worker_keys_file))
        secret = keys.get(body.gpu_uuid)
        if (not isinstance(secret, str) or not re.fullmatch('[a-f0-9]{64}', secret)
                or not re.fullmatch('Bearer [a-f0-9]{64}', authorization)
                or not hmac.compare_digest(authorization[7:], secret)):
            raise ServiceError('EXECUTION_WORKER_DENIED', 'GPU execution credential rejected', 403)
        kind, token = body.attempt.split('.', 1)
        table = TABLE[kind]
        with db.transaction() as conn:
            conn.execute('SELECT id FROM maintenance WHERE id=1 FOR UPDATE')
            row = conn.execute(f'''SELECT * FROM {table} WHERE token=%s AND execution_guard
                AND dispatch_route=%s FOR UPDATE''', (token, body.route)).fetchone()
            if row and body.finished and (row['gpu_uuid'], row['gpu_instance']) == (body.gpu_uuid, body.instance):
                end_activity = row['state'] == 'UNKNOWN' or (kind == 'task' and row['state'] == 'DISPATCHING')
                conn.execute(f'''UPDATE {table} SET gpu_finished_at=clock_timestamp(), updated_at=now(),
                    state=CASE WHEN %s THEN 'COMPLETED' ELSE state END,
                    resolution_reason=CASE WHEN state='UNKNOWN' THEN 'GPU confirmed execution ended' ELSE resolution_reason END
                    WHERE token=%s AND gpu_finished_at IS NULL''', (end_activity, token))
                return Response(status_code=204)
            if row and not body.finished and row['state'] == 'DISPATCHING' and row['gpu_uuid'] is None:
                # Recheck the original dispatch window. A delayed gateway retry cannot start expired work.
                active = ("SELECT 1 FROM tasks WHERE token=%s AND state='RUNNING' "
                          "AND deadline>clock_timestamp() AND lease_until>clock_timestamp()" if kind == 'task'
                          else 'SELECT 1 FROM sync_activity WHERE token=%s AND deadline>clock_timestamp()')
                if conn.execute(active, (token,)).fetchone():
                    conn.execute(f'UPDATE {table} SET gpu_uuid=%s,gpu_instance=%s WHERE token=%s',
                                 (body.gpu_uuid, body.instance, token))
                    return Response(status_code=204)
        raise ServiceError('EXECUTION_NOT_GRANTED', 'Attempt unavailable or already claimed', 409)

    return routes
