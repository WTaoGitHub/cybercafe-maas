"""Read-only GPUStack observation with a single durable budget publisher."""
import asyncio
import json
import logging
import os
from pathlib import Path
import time

import httpx
import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from .capacity import Capacity, Policy, catalog_routes, limits, policy_hash, ready_candidates
from .config import private_text
from .gpu_daily_watch import listing
from .guard_topology import verify_processes


LOG = logging.getLogger(__name__)
RESOURCES = ('workers', 'models', 'model-instances', 'model-routes', 'model-route-targets')
INTERVAL, READ_TIMEOUT, SETTLE, TTL = 10, 5, 5, 30
WRITER_LOCK = 842125224


async def publish(conn, policy, observed, payload):
    async with conn.transaction():
        await conn.execute("SET LOCAL lock_timeout = '5s'")
        await conn.execute("SET LOCAL statement_timeout = '15s'")
        await conn.execute('SELECT id FROM maintenance WHERE id=1 FOR UPDATE')
        cursor = await conn.execute('''INSERT INTO capacity_snapshot(id, policy_hash, observed_at, expires_at, budget)
            SELECT 1, %(policy)s, %(observed)s, %(observed)s + %(ttl)s * interval '1 second', %(budget)s
            WHERE clock_timestamp() < %(observed)s + %(ttl)s * interval '1 second'
            ON CONFLICT (id) DO UPDATE SET policy_hash=excluded.policy_hash, observed_at=excluded.observed_at,
              expires_at=excluded.expires_at, budget=excluded.budget, generation=capacity_snapshot.generation+1
            WHERE capacity_snapshot.observed_at < excluded.observed_at
              AND excluded.expires_at > clock_timestamp()
            RETURNING generation''', {'policy': policy, 'observed': observed, 'ttl': TTL, 'budget': Jsonb(payload)})
        return await cursor.fetchone()


def status(db):
    with db.transaction() as conn:
        conn.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY')
        row = conn.execute('''SELECT generation, policy_hash, extract(epoch FROM observed_at) observed_at,
            extract(epoch FROM expires_at) expires_at, clock_timestamp() < expires_at fresh, budget
            FROM capacity_snapshot WHERE id=1''').fetchone()
        if row and row['fresh']:
            from .capacity_admission import isolate_budget
            from .quarantine import unresolved
            isolate_budget(row['budget'], unresolved(conn))
    if row is None:
        return {'mode': 'observe', 'state': 'waiting_for_snapshot', 'global': limits(0, 0), 'pools': {}}
    budget = row.pop('budget')
    fresh = row.pop('fresh')
    if not fresh:
        budget['global'].update(limits(0, 0))
        for pool in budget['pools'].values():
            pool.update(limits(0, 0), reason='expired', ready_ids=[], pending_ids=[])
            pool['ready_gpus'] = {}
            if 'processes' in pool:
                pool['processes'] = {}
                pool['route_gpus'] = []
                pool.pop('guard_topology', None)
            if 'replica_routes' in pool:
                pool['replica_routes'] = {}
    row.update(observed_at=float(row['observed_at']), expires_at=float(row['expires_at']))
    return {'mode': 'observe', 'state': 'fresh' if fresh else 'expired', **row, **budget}


def event_identity(kind, row):
    if kind == 'workers':
        devices = (row.get('status') or {}).get('gpu_devices') or []
        return (row.get('state'), row.get('unreachable'), row.get('maintenance'), row.get('cluster_id'),
                row.get('system_reserved'),
                [(gpu.get('index'), gpu.get('uuid'), gpu.get('name'), (gpu.get('memory') or {}).get('total'))
                 for gpu in devices])
    if kind == 'model-instances':
        return tuple(row.get(key) for key in ('state', 'model_id', 'worker_id', 'gpu_indexes',
                                               'distributed_servers', 'last_restart_time', 'restart_count',
                                               'computed_resource_claim'))
    return row  # Route changes are infrequent and include target/backend eligibility changes.


async def watch_changes(client, kind, changed):
    while True:
        seen = {}
        try:
            async with client.stream('GET', kind, params={'watch': 'true'}, timeout=None) as response:
                response.raise_for_status()
                changed.set()
                async for line in response.aiter_lines():
                    if not line.strip():
                        continue
                    event = json.loads(line)
                    row = event['data']
                    ident = row['id']
                    if event['type'] == 3:
                        seen.pop(ident, None)
                        changed.set()
                    elif event['type'] in (1, 2):
                        marker = event_identity(kind, row)
                        if seen.get(ident) != marker:
                            seen[ident] = marker
                            changed.set()
        except (httpx.HTTPError, ValueError, KeyError, TypeError, AttributeError):
            pass
        # Full polling remains authoritative even when a watch is unavailable.
        await asyncio.sleep(INTERVAL)


async def read_snapshot(client):
    async with asyncio.timeout(READ_TIMEOUT):
        reads = [asyncio.create_task(listing(client, kind)) for kind in RESOURCES]
        try:
            return await asyncio.gather(*reads)
        finally:
            for task in reads:
                task.cancel()
            await asyncio.gather(*reads, return_exceptions=True)


def gateway_candidates(policy, names, native, candidates):
    # Compute before gateway/guard verification: an isolated, paused process
    # cannot answer a challenge and must not prevent discovery of its peers.
    workers, models, instances, routes, _ = native
    result = {}
    for key, pool in policy.pools.items():
        if not pool.guard_backend_version:
            continue
        model = models.get(pool.model_id, {})
        for route_id in pool.routes.values():
            peers = {}
            if model.get('backend_version') == pool.guard_backend_version and not (
                    model.get('generic_proxy') or routes.get(route_id, {}).get('generic_proxy')):
                for ident, (worker_id, gpu, *_) in candidates[key].items():
                    instance = instances[ident]
                    if (instance.get('backend_version') == pool.guard_backend_version
                            and instance.get('worker_ip') == workers[worker_id].get('ip')):
                        peers[str(ident)] = [instance['worker_ip'], instance['port'], gpu]
            result[str(route_id)] = dict(model=pool.model_id, name=names[route_id], peers=peers)
    return result


async def observe(settings, path, stop, report):
    policy = Policy.model_validate_json(private_text(path))
    token = private_text(Path(os.environ['MAAS_CAPACITY_GPUSTACK_KEY_FILE']))
    base = settings.gpustack_url.rstrip('/') + '/v2/'
    changed = asyncio.Event()
    async with httpx.AsyncClient(base_url=base, headers={'Authorization': 'Bearer ' + token},
                                 timeout=READ_TIMEOUT, trust_env=False, follow_redirects=False) as client:
        streams = [asyncio.create_task(watch_changes(client, kind, changed)) for kind in RESOURCES if kind != 'models']
        try:
            while not stop.is_set():
                try:
                    async with await psycopg.AsyncConnection.connect(settings.database_url, autocommit=True,
                                                                      row_factory=dict_row, connect_timeout=5) as conn:
                        cursor = await conn.execute('SELECT pg_try_advisory_lock(%s) AS acquired', (WRITER_LOCK,))
                        if (await cursor.fetchone())['acquired']:
                            # Use this same session for publishing. Losing the writer lock invalidates the session.
                            await sample(client, conn, settings.catalog_file, base, policy, changed, stop, report, settings.pp_ocr_worker_keys_file)
                except psycopg.Error:
                    LOG.warning('capacity_database_unavailable')
                await asyncio.sleep(1)
        finally:
            for stream in streams:
                stream.cancel()
            await asyncio.gather(*streams, return_exceptions=True)


async def sample(client, conn, catalog_file, source, policy, changed, stop, report, guard_keys=None):
    last_hash = None
    reported = None
    while not stop.is_set():
        changed.clear()
        try:
            names = catalog_routes(policy, json.loads(catalog_file.read_text()))
            revision = policy_hash(policy, names, source)
            if revision != last_hash:
                capacity = Capacity(policy, SETTLE, TTL)
                last_hash = revision
            cursor = await conn.execute('SELECT clock_timestamp() AS observed_at')
            observed = (await cursor.fetchone())['observed_at']
            native = await read_snapshot(client)
            candidates, reasons = ready_candidates(policy, names, *native)
            intent_path = os.getenv('MAAS_PP_GATEWAY_INTENT_FILE')
            if intent_path:
                route_candidates = gateway_candidates(policy, names, native, candidates)
            proofs = await verify_processes(policy, names, native, candidates, reasons, guard_keys,
                                            os.getenv('MAAS_GATEWAY_SCOPE_FILE'))
            # The native lists and catalog are not a transactional snapshot.
            if changed.is_set() or names != catalog_routes(policy, json.loads(catalog_file.read_text())):
                raise ValueError('Bindings or topology changed during capacity observation')
            budget = capacity.update(observed.timestamp(), candidates, reasons, proofs)
            if intent_path:
                cursor = await conn.execute('SELECT DISTINCT isolated_gpu_uuid FROM inference_slots '
                                            'WHERE isolated_gpu_uuid IS NOT NULL')
                intent = dict(observed=observed.timestamp(), routes=route_candidates,
                              isolated=[r['isolated_gpu_uuid'] for r in await cursor.fetchall()])
                path = Path(intent_path)
                try:
                    draft = path.with_suffix('.new')
                    draft.write_text(json.dumps(intent))
                    draft.replace(path)
                except OSError:
                    LOG.error('pp_gateway_intent_write_failed')
        except (httpx.HTTPError, TimeoutError, OSError, ValueError, KeyError, TypeError, AttributeError):
            if last_hash is not None:
                capacity.interrupted()
            code = 'capacity_observation_unavailable'
        else:
            code = None if await publish(conn, revision, observed, budget) else 'capacity_observation_stale'
        if code != reported and code:
            LOG.warning(code)
        reported = code
        await asyncio.to_thread(report)
        # Coalesce concurrent events and keep the stop check responsive.
        until = time.monotonic() + INTERVAL
        while not stop.is_set() and not changed.is_set() and time.monotonic() < until:
            try:
                await asyncio.wait_for(changed.wait(), min(1, max(0, until - time.monotonic())))
            except TimeoutError:
                pass
        if changed.is_set():
            await asyncio.sleep(1)
