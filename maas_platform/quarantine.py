"""Dispatch scopes are recorded before send and survive catalog and route changes."""
import hashlib
import json
import re

from psycopg.types.json import Jsonb


def save_scope(c, pool):
    processes = pool.get('processes')
    if not processes:
        return None
    topology = pool['guard_topology']
    document = json.dumps([processes,topology], sort_keys=True, separators=(',', ':'))
    digest = hashlib.sha256(document.encode()).hexdigest()
    c.execute('INSERT INTO execution_scopes(id,processes,topology) VALUES(%s,%s,%s) ON CONFLICT DO NOTHING',
              (digest, Jsonb(processes), Jsonb(topology)))
    return digest


def unresolved(connection):
    return connection.execute("""SELECT t.model,t.project,true AS asynchronous,b.dispatch_route,b.dispatch_pool_id,b.dispatch_gpu_uuid,b.gpu_uuid,
          coalesce(b.isolated_gpu_uuid, CASE WHEN b.guard_scope IS NOT NULL THEN b.gpu_uuid END) AS isolated_gpu_uuid
          FROM backend_activity b JOIN tasks t ON t.id=b.task_id WHERE b.state='UNKNOWN'
          UNION ALL SELECT model,project,false,dispatch_route,dispatch_pool_id,dispatch_gpu_uuid,gpu_uuid,
          coalesce(isolated_gpu_uuid, CASE WHEN guard_scope IS NOT NULL THEN gpu_uuid END)
          FROM sync_activity WHERE state='UNKNOWN'""").fetchall()


def blocks(rows, name, route, pool_id, gpu_uuid=None):
    for row in rows:
        if row['dispatch_route'] is None:
            return True
        isolated = row.get('isolated_gpu_uuid')
        if isolated:
            if gpu_uuid is None or gpu_uuid == isolated:
                return True
            continue
        pinned = row['dispatch_gpu_uuid']
        if pinned and (gpu_uuid is None or gpu_uuid == pinned):
            return True
        if pinned and pinned == row['gpu_uuid']:
            continue
        if gpu_uuid and not pinned and row['gpu_uuid'] is None:
            # A legacy unlocated attempt could occupy a GPU shared across pools.
            return True
        if (row['model'] == name or (route and row['dispatch_route'] == route)
                or (pool_id is not None and row['dispatch_pool_id'] == pool_id)
                or (gpu_uuid and gpu_uuid == row['gpu_uuid'])):
            return True
    return False

def isolate_unknown(db, kind, token, gpu, evidence):
    """Record exclusive landing evidence without claiming that execution has ended."""
    from .pp_ocr_execution import TABLE
    if not re.fullmatch(r'GPU-[0-9a-f]{8}(-[0-9a-f]{4}){3}-[0-9a-f]{12}', gpu or ''):
        raise ValueError('Provide the physical GPU UUID')
    if len(evidence.strip()) < 10:
        raise ValueError('Provide evidence covering every possible original attempt and retry')
    table = TABLE[kind]
    with db.transaction() as c:
        c.execute('SELECT id FROM maintenance WHERE id=1 FOR UPDATE')
        return bool(c.execute(f"""UPDATE {table} SET isolated_gpu_uuid=%s,isolation_evidence=%s
            WHERE token=%s AND state='UNKNOWN' AND dispatch_route IS NOT NULL
              AND (gpu_uuid IS NULL OR gpu_uuid=%s)
              AND (dispatch_gpu_uuid IS NULL OR dispatch_gpu_uuid=%s)
              AND (isolated_gpu_uuid IS NULL OR (isolated_gpu_uuid=%s AND isolation_evidence=%s))
            RETURNING token""", (gpu,evidence,token,gpu,gpu,gpu,evidence)).fetchone())
