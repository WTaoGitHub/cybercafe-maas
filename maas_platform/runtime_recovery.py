"""Challenge the original guarded runtime and optionally settle proven terminations."""
import hmac
import json
import re
from secrets import token_hex
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, StringConstraints
from psycopg.types.json import Jsonb

from .errors import ServiceError
from .runtime_proof import GPU, HEX, signature, valid_identity


class Binding(BaseModel):
    model_config = ConfigDict(extra='forbid')
    identity: dict
    signature: Annotated[str, StringConstraints(pattern=r'^[a-f0-9]{64}$')]


BOOT = r'[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}'
BootID = Annotated[str, StringConstraints(pattern='^' + BOOT + '$')]


class HostPoll(BaseModel):
    boot_id: BootID | None = None
    model_config = ConfigDict(extra='forbid')
    operation: Literal['runtime_poll']
    reconnected: bool = False
    gpu_uuid: Annotated[str, StringConstraints(pattern=f"^{GPU.pattern}$")]


class HostReport(BaseModel):
    boot_id: BootID | None = None
    model_config = ConfigDict(extra='forbid')
    operation: Literal['runtime_report']
    gpu_uuid: Annotated[str, StringConstraints(pattern=f"^{GPU.pattern}$")]
    instance: Annotated[str, StringConstraints(pattern=r'^[a-f0-9]{32}$')]
    nonce: Annotated[str, StringConstraints(pattern=r'^[a-f0-9]{64}$')]
    outcome: Literal['present', 'unconfirmed', 'terminated']
    signature: Annotated[str, StringConstraints(pattern=r'^[a-f0-9]{64}$')]


def host_key(settings, gpu, boot=None):
    from .config import private_text

    if boot is not None:
        if not settings.runtime_boot_keys_dir or not GPU.fullmatch(gpu) or not re.fullmatch(BOOT, boot):
            return None
        path = settings.runtime_boot_keys_dir / f'{gpu}.{boot}.key'
        if path.is_symlink() or not path.exists():
            return None
        value = private_text(path)
        if not HEX.fullmatch(value):
            return None
    else:
        if settings.runtime_proof_keys_file is None:
            return None
        keys = json.loads(private_text(settings.runtime_proof_keys_file))
        value = keys.get(gpu) if isinstance(keys, dict) else None
        if not isinstance(value, str) or not HEX.fullmatch(value) or list(keys.values()).count(value) != 1:
            return None
    worker_keys = settings.pp_ocr_worker_keys_file
    if worker_keys and value in json.loads(private_text(worker_keys)).values():
        return None  # An inference container may never authenticate as a host witness.
    return value


def binding_payload(gpu, instance, route, identity):
    return {'gpu_uuid': gpu, 'instance': instance, 'route': route, 'identity': identity}


def check_binding(settings, body):
    binding = body.runtime
    if binding is None:
        return None
    if not valid_identity(binding.identity):
        raise ServiceError('RUNTIME_PROOF_DENIED', 'Invalid runtime binding', 403)
    boot = binding.identity['boot']
    path = settings.runtime_boot_keys_dir and settings.runtime_boot_keys_dir / f'{body.gpu_uuid}.{boot}.key'
    signer_boot = boot if path and (path.exists() or path.is_symlink()) else None
    key = host_key(settings, body.gpu_uuid, signer_boot)
    if key is None:
        return None  # This GPU has not enrolled in runtime evidence collection.
    payload = binding_payload(body.gpu_uuid, body.instance, body.route, binding.identity)
    if not hmac.compare_digest(
            binding.signature, signature(key, 'bind', payload)):
        raise ServiceError('RUNTIME_PROOF_DENIED', 'Invalid runtime binding', 403)
    return binding.identity, signer_boot


def register(conn, body, binding):
    identity, signer_boot = binding
    row = conn.execute('''INSERT INTO execution_runtime(instance,gpu_uuid,route,identity,signer_boot)
        VALUES (%s,%s,%s,%s,%s) ON CONFLICT(instance) DO UPDATE
        SET terminal_proof=NULL,challenge=NULL,challenge_until=NULL
        WHERE execution_runtime.gpu_uuid=excluded.gpu_uuid AND execution_runtime.route=excluded.route
          AND execution_runtime.identity=excluded.identity
          AND execution_runtime.signer_boot IS NOT DISTINCT FROM excluded.signer_boot
        RETURNING instance''', (body.instance, body.gpu_uuid, body.route, Jsonb(identity), signer_boot)).fetchone()
    if not row:
        raise ServiceError('RUNTIME_PROOF_DENIED', 'Conflicting runtime identity', 409)
    return row['instance']


QUALIFIED_UNKNOWN = """state='UNKNOWN' AND execution_guard AND (guard_scope IS NOT NULL OR dispatch_gpu_uuid=gpu_uuid)
    AND NOT guard_conflict AND gpu_finished_at IS NULL AND runtime_instance IS NOT NULL"""
ACTIVITY_TABLES = ('backend_activity', 'sync_activity')


def settle_runtime(conn, row, nonce):
    for table in ACTIVITY_TABLES:
        conn.execute(f"""UPDATE {table} SET state='RESOLVED', updated_at=clock_timestamp(),
            resolution_reason='Original guarded runtime terminated (host challenge '||%s||')'
            WHERE runtime_instance=%s AND gpu_instance=%s AND gpu_uuid=%s AND dispatch_route=%s
              AND {QUALIFIED_UNKNOWN}""",
                     (nonce, row['instance'], row['instance'], row['gpu_uuid'], row['route']))


def apply_verified(db, settings, gpu=None, boot=None):
    if not (settings.runtime_boot_keys_dir or settings.runtime_proof_keys_file):
        return
    apply_routes = settings.automatic_runtime_routes()
    if not apply_routes:
        return
    with db.transaction() as conn:
        rows = conn.execute(f"""SELECT r.* FROM execution_runtime r
            WHERE (%s::text IS NULL OR (r.gpu_uuid=%s AND r.signer_boot IS NOT DISTINCT FROM %s::text))
              AND r.route=ANY(%s) AND r.terminal_proof IS NOT NULL AND r.instance IN (
                  SELECT runtime_instance FROM backend_activity WHERE {QUALIFIED_UNKNOWN} UNION ALL
                  SELECT runtime_instance FROM sync_activity WHERE {QUALIFIED_UNKNOWN})
            ORDER BY r.next_check_at,r.instance LIMIT 4""", (gpu, gpu, boot, list(apply_routes))).fetchall()
        if not rows:
            return
        conn.execute('SELECT id FROM maintenance WHERE id=1 FOR UPDATE')
        for candidate in rows:
            row = conn.execute('SELECT * FROM execution_runtime WHERE instance=%s FOR UPDATE',
                               (candidate['instance'],)).fetchone()
            if not row or not row['terminal_proof']:
                continue  # A new grant invalidated a candidate seen before locking.
            payload = dict(row['terminal_proof']) if isinstance(row['terminal_proof'], dict) else {}
            seen = payload.pop('signature', None)
            expected = {**binding_payload(row['gpu_uuid'], row['instance'], row['route'], row['identity']),
                        'outcome': 'terminated', 'nonce': payload.get('nonce')}
            try:
                key = host_key(settings, row['gpu_uuid'], row['signer_boot'])
            except (OSError, ValueError):
                key = None
            if (key and payload == expected and isinstance(seen, str) and HEX.fullmatch(seen)
                    and isinstance(expected['nonce'], str) and HEX.fullmatch(expected['nonce'])
                    and hmac.compare_digest(seen, signature(key, 'inspect', expected))):
                settle_runtime(conn, row, expected['nonce'])
            else:
                # Keep the quarantine and let other candidates advance past this batch.
                conn.execute("UPDATE execution_runtime SET next_check_at=clock_timestamp()+interval '60 seconds' WHERE instance=%s",
                             (row['instance'],))


def poll(db, gpu, reconnected, boot=None):
    # Lock only a bounded batch of due runtimes, even if the host polls concurrently.
    with db.transaction() as conn:
        acquired = conn.execute('SELECT pg_try_advisory_xact_lock(842125225) AS acquired').fetchone()
        if not acquired['acquired']:
            return {'checks': []}
        in_flight = conn.execute("""SELECT count(*) AS total FROM execution_runtime
            WHERE challenge_until>clock_timestamp()""").fetchone()['total']
        batch = min(4, 32-in_flight)
        if batch <= 0:
            return {'checks': []}
        rows = conn.execute(f"""SELECT r.instance,r.identity,r.route FROM execution_runtime r
            WHERE r.gpu_uuid=%s AND r.signer_boot IS NOT DISTINCT FROM %s::text
              AND (r.challenge_until IS NULL OR r.challenge_until<=clock_timestamp())
              AND (%s OR r.next_check_at<=clock_timestamp())
              AND r.terminal_proof IS NULL
              AND (EXISTS(SELECT 1 FROM backend_activity WHERE runtime_instance=r.instance
                       AND {QUALIFIED_UNKNOWN} AND unknown_since<=clock_timestamp()-interval '30 seconds')
                OR EXISTS(SELECT 1 FROM sync_activity WHERE runtime_instance=r.instance
                       AND {QUALIFIED_UNKNOWN} AND unknown_since<=clock_timestamp()-interval '30 seconds'))
            ORDER BY r.next_check_at,r.instance LIMIT %s FOR UPDATE OF r SKIP LOCKED""", (gpu, boot, reconnected, batch)).fetchall()
        for row in rows:
            row['nonce'] = token_hex(32)
            conn.execute("""UPDATE execution_runtime SET challenge=%s,
                challenge_until=clock_timestamp()+interval '25 seconds',
                next_check_at=clock_timestamp()+least(300,30*(1<<least(attempts,4)))*interval '1 second',
                attempts=least(attempts+1,5) WHERE instance=%s""", (row['nonce'], row['instance']))
        return {'checks': rows}


def report(db, value, key, apply_routes):
    with db.transaction() as conn:
        # Same lock ordering as grants and normal GPU finish receipts.
        conn.execute('SELECT id FROM maintenance WHERE id=1 FOR UPDATE')
        row = conn.execute('''SELECT * FROM execution_runtime WHERE instance=%s AND gpu_uuid=%s
            AND challenge=%s AND challenge_until>clock_timestamp() FOR UPDATE''',
                           (value.instance, value.gpu_uuid, value.nonce)).fetchone()
        if not row or (value.boot_id is not None and row['identity']['boot'] != value.boot_id):
            raise ServiceError('RUNTIME_CHALLENGE_EXPIRED', 'Runtime challenge is no longer active', 409)
        if row['signer_boot'] != value.boot_id:
            raise ServiceError('RUNTIME_PROOF_DENIED', 'Runtime requires its enrolled boot signer', 403)
        payload = {**binding_payload(value.gpu_uuid, value.instance, row['route'], row['identity']),
                   'nonce': value.nonce, 'outcome': value.outcome}
        if not hmac.compare_digest(value.signature, signature(key, 'inspect', payload)):
            raise ServiceError('RUNTIME_PROOF_DENIED', 'Invalid runtime evidence', 403)

        if value.outcome == 'terminated':
            proof = Jsonb({**payload, 'signature': value.signature})
            conn.execute('''UPDATE execution_runtime SET terminal_proof=%s WHERE instance=%s''',
                         (proof, value.instance))
        if value.outcome == 'terminated' and row['route'] in apply_routes:
            settle_runtime(conn, row, value.nonce)
        conn.execute("""UPDATE execution_runtime SET challenge=NULL,challenge_until=NULL,
            last_checked_at=clock_timestamp(),last_outcome=%s,next_check_at=clock_timestamp()+interval '30 seconds',
            attempts=0 WHERE instance=%s""", (value.outcome, value.instance))


def handle_host(db, settings, value, authorization):
    key = host_key(settings, value.gpu_uuid, value.boot_id)
    if key is None or not authorization.isascii() or not hmac.compare_digest(authorization, 'Bearer '+key):
        raise ServiceError('RUNTIME_PROOF_DENIED', 'Host credential rejected', 403)
    if isinstance(value, HostPoll):
        apply_verified(db, settings, value.gpu_uuid, value.boot_id)
        return poll(db, value.gpu_uuid, value.reconnected, value.boot_id)
    report(db, value, key, settings.automatic_runtime_routes())
