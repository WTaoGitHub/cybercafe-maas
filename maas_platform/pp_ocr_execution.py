"""Worker-authenticated start and finish for guarded OCR routes."""
import hmac
import json
import re
from typing import Annotated

from fastapi import APIRouter, Header, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel, StringConstraints

from .errors import ServiceError
from .quarantine import blocks, unresolved
from .runtime_proof import AUTHORITY_PATH
from .runtime_recovery import Binding, HostPoll, HostReport, check_binding, handle_host, register

PATH = AUTHORITY_PATH
ATTEMPT_HEADER = 'X-MaaS-Execution-Attempt'
TABLE = {'task': 'backend_activity', 'sync': 'sync_activity'}
HexID = Annotated[str, StringConstraints(pattern=r'^[a-f0-9]{32}$')]


class GPUEvent(BaseModel):
    attempt: Annotated[str, StringConstraints(pattern=r'^(task|sync)\.[0-9a-f]{32}$')]
    gpu_uuid: Annotated[str, StringConstraints(pattern=r'^GPU-[0-9a-f]{8}(-[0-9a-f]{4}){3}-[0-9a-f]{12}$')]
    instance: HexID
    route: Annotated[str, StringConstraints(min_length=1, max_length=128)]
    finished: bool
    runtime: Binding | None = None


def finished(db, kind, token):
    with db.transaction() as conn:
        return bool(conn.execute(f'SELECT 1 FROM {TABLE[kind]} WHERE token=%s AND gpu_finished_at IS NOT NULL',
                                 (token,)).fetchone())


def check_success_headers(db, kind, token):
    """A successful upstream response before start contradicts a guard-only route."""
    table = TABLE[kind]
    with db.transaction() as c:
        row = c.execute(f'''SELECT gpu_finished_at IS NOT NULL AS ended,
            guard_scope IS NOT NULL AND gpu_uuid IS NULL AS conflict FROM {table} WHERE token=%s''',
                        (token,)).fetchone()
        if not row or not row['conflict']:
            return bool(row and row['ended'])
        c.execute('SELECT id FROM maintenance WHERE id=1 FOR UPDATE')
        c.execute(f"""UPDATE {table} SET guard_conflict=true,state='UNKNOWN',updated_at=now(),
                 resolution_reason='Upstream success without a GPU grant'
                 WHERE token=%s AND guard_scope IS NOT NULL AND gpu_uuid IS NULL AND
                 (state IN ('DISPATCHING','UNKNOWN') OR
                  (state='RESOLVED' AND resolution_reason='Execution closed before any GPU grant'))""", (token,))
        return False


def record_finish(conn, table, row, reason):
    # Caller holds the maintenance lock and the original attempt row lock.
    end_activity = row['state'] == 'UNKNOWN' or (table == 'backend_activity' and row['state'] == 'DISPATCHING')
    conn.execute(f'''UPDATE {table} SET gpu_finished_at=clock_timestamp(), updated_at=now(),
        state=CASE WHEN %s THEN 'COMPLETED' ELSE state END,
        resolution_reason=CASE WHEN state='UNKNOWN' THEN %s ELSE resolution_reason END
        WHERE token=%s AND gpu_finished_at IS NULL''', (end_activity, reason, row['token']))


def router(db, settings):
    routes = APIRouter()

    @routes.post(PATH, include_in_schema=False, status_code=204)
    def event(body: GPUEvent | HostPoll | HostReport, authorization: str = Header(default='')):
        from .config import private_text

        if not isinstance(body, GPUEvent):
            result = handle_host(db, settings, body, authorization)
            return JSONResponse(result) if result is not None else Response(status_code=204)

        if settings.pp_ocr_worker_keys_file is None:
            raise ServiceError('EXECUTION_GUARD_DISABLED', 'Execution guard is disabled', 404)
        keys = json.loads(private_text(settings.pp_ocr_worker_keys_file))
        secret = keys.get(body.gpu_uuid)
        if (not isinstance(secret, str) or not re.fullmatch('[a-f0-9]{64}', secret)
                or not re.fullmatch('Bearer [a-f0-9]{64}', authorization)
                or not hmac.compare_digest(authorization[7:], secret)):
            raise ServiceError('EXECUTION_WORKER_DENIED', 'GPU execution credential rejected', 403)
        identity = check_binding(settings, body) if not body.finished else None
        kind, token = body.attempt.split('.', 1)
        table = TABLE[kind]
        with db.transaction() as conn:
            maintenance = conn.execute('SELECT capacity_policy_hash FROM maintenance WHERE id=1 FOR UPDATE').fetchone()
            row = conn.execute(f'''SELECT * FROM {table} WHERE token=%s AND execution_guard
                AND dispatch_route=%s FOR UPDATE''', (token, body.route)).fetchone()
            if row and body.finished and (row['gpu_uuid'], row['gpu_instance']) == (body.gpu_uuid, body.instance):
                record_finish(conn, table, row, 'GPU confirmed execution ended')
                return Response(status_code=204)
            if (row and not body.finished and row['state'] == 'DISPATCHING' and row['gpu_uuid'] is None
                    and row['dispatch_gpu_uuid'] in (None, body.gpu_uuid)):
                # Recheck the original dispatch window. A delayed gateway retry cannot start expired work.
                active = ("SELECT model FROM tasks WHERE token=%s AND state='RUNNING' "
                          "AND deadline>clock_timestamp() AND lease_until>clock_timestamp()" if kind == 'task'
                          else 'SELECT model FROM sync_activity WHERE token=%s AND deadline>clock_timestamp()')
                eligible = conn.execute(active, (token,)).fetchone()
                scope = row['guard_scope']
                if scope is not None:
                    # Check original authorization and current placement, including a rolling replacement.
                    granted = conn.execute("""SELECT 1 FROM execution_scopes s,capacity_snapshot b,
                        LATERAL jsonb_each(b.budget->'pools') p
                        WHERE s.id=%s AND s.processes->>%s=%s AND b.id=1
                          AND b.policy_hash=%s AND b.expires_at>clock_timestamp()
                          AND (p.value->>'model_id')::int=%s AND p.value->'processes'->>%s=%s""",
                        (scope,body.instance,body.gpu_uuid,maintenance['capacity_policy_hash'],
                         row['dispatch_pool_id'],body.instance,body.gpu_uuid)).fetchone()
                    if not granted:
                        raise ServiceError('EXECUTION_NOT_GRANTED','Process outside verified dispatch scope',409)
                unknown = unresolved(conn)
                must_check = scope is not None or row['dispatch_gpu_uuid'] is not None
                isolated = any(r['isolated_gpu_uuid'] == body.gpu_uuid for r in unknown)
                if eligible and not isolated and (not must_check or not blocks(
                        unknown, eligible['model'], body.route, row['dispatch_pool_id'], body.gpu_uuid)):
                    fenced = scope is not None or row['dispatch_gpu_uuid'] == body.gpu_uuid
                    runtime = register(conn, body, identity) if fenced and identity else None
                    conn.execute(f'UPDATE {table} SET gpu_uuid=%s,gpu_instance=%s,runtime_instance=%s WHERE token=%s',
                                 (body.gpu_uuid, body.instance, runtime, token))
                    return Response(status_code=204)
        raise ServiceError('EXECUTION_NOT_GRANTED', 'Attempt unavailable or already claimed', 409)

    return routes
