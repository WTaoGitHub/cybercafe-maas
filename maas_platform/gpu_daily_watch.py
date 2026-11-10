"""Collect worker and instance observations for E17 daily reports."""
import asyncio
from datetime import datetime, timedelta
import json
import logging
import os
from pathlib import Path
import time
from types import SimpleNamespace
from uuid import uuid4

import httpx
import psycopg
from psycopg.types.json import Jsonb

from .db import Database
from .gpu_daily import MAX_GAP, seal
from .gpu_time import BEIJING, snapshot


LOG = logging.getLogger(__name__)
KINDS = ('workers', 'model-instances')


def cards_for(workers, instances, logical_names):
    online = {uuid: [] for uuid, ready in snapshot(workers.values()).items() if ready}
    devices = {(worker['id'], gpu.get('index')): gpu.get('uuid')
               for worker in workers.values() if worker['state'] == 'ready'
               for gpu in (worker.get('status') or {}).get('gpu_devices') or []}
    for replica in instances.values():
        if replica['state'] != 'running':
            continue
        name = replica['model_name']
        model_id = str(replica['model_id'])
        bindings = [replica, *((replica.get('distributed_servers') or {}).get('subordinate_workers') or [])]
        for binding in bindings:
            if binding['state'] != 'running':
                continue
            for index in binding.get('gpu_indexes') or []:
                uuid = devices.get((binding.get('worker_id'), index))
                if uuid in online:
                    for logical in logical_names.get(name, [None]):
                        model = {'logical_model_name': logical, 'gpustack_deployment_id': model_id,
                                 'gpustack_deployment_name': name}
                        if model not in online[uuid]:
                            online[uuid].append(model)
    for models in online.values():
        models.sort(key=lambda item: (item['gpustack_deployment_id'], item['logical_model_name'] or ''))
    return online


async def listing(client, kind):
    rows = {}
    for page in range(1, 1001):
        response = await client.get(kind, params={'page': page, 'perPage': 100})
        response.raise_for_status()
        data = response.json()
        for row in data['items']:
            if row['id'] in rows:
                raise ValueError('Duplicate object while paging')
            rows[row['id']] = row
        pagination = data['pagination']
        if page >= pagination['totalPage']:
            if len(rows) != pagination['total']:
                raise ValueError('List changed while paging')
            return rows
    raise ValueError('GPUStack page limit exceeded')


async def watch(client, kind, queue, signals=None):
    try:
        async with client.stream('GET', kind, params={'watch': 'true'}) as response:
            response.raise_for_status()
            queue.put_nowait((kind, None))
            async for line in response.aiter_lines():
                if line.strip():
                    queue.put_nowait((kind, json.loads(line)))
                    if signals is not None:
                        signals.changed.set()
        raise ConnectionError('GPUStack watch ended')
    finally:
        if signals is not None:
            signals.session = None
            signals.changed.set()

def persist(db, at, session, cards):
    with db.transaction() as conn:
        conn.execute('INSERT INTO e17.daily_observations VALUES (%s,%s,%s) ON CONFLICT (at) DO NOTHING',
                     (at, session, Jsonb(cards)))


async def observe(db, client, catalog, signals=None):
    while True:
        queue = asyncio.Queue(maxsize=4096)
        streams = [asyncio.create_task(watch(client, kind, queue, signals)) for kind in KINDS]
        try:
            connected = set()
            async with asyncio.timeout(35):
                while len(connected) < len(KINDS):
                    kind, event = await queue.get()
                    if event is None:
                        connected.add(kind)
                workers, instances = await asyncio.gather(*(listing(client, kind) for kind in KINDS))
            rows = dict(zip(KINDS, (workers, instances)))
            session = uuid4()
            if any(task.done() for task in streams):
                raise ConnectionError('GPUStack watch disconnected during initial listing')
            if signals is not None:
                signals.session = session
                signals.changed.set()
            last_at, last_cards = 0, None
            reconcile_at = time.monotonic() + 300
            names = {}
            for logical, model in json.loads(catalog.read_text()).items():
                if model.get('upstream_model'):
                    names.setdefault(model['upstream_model'], []).append(logical)
            while True:
                if any(task.done() for task in streams):
                    raise ConnectionError('GPUStack watch disconnected')
                try:
                    kind, event = await asyncio.wait_for(queue.get(), timeout=1)
                    if event is not None:
                        row = event['data']
                        if event['type'] == 3:
                            rows[kind].pop(row['id'], None)
                        elif event['type'] in (1, 2):
                            rows[kind][row['id']] = row
                except TimeoutError:
                    pass
                if time.monotonic() >= reconcile_at:
                    async with asyncio.timeout(35):
                        fresh = await asyncio.gather(*(listing(client, kind) for kind in KINDS))
                    if cards_for(*fresh, names) != last_cards:
                        break  # The current state diverged. Open a new coverage session.
                    reconcile_at = time.monotonic() + 300
                cards = cards_for(workers, instances, names)
                at = int(time.time())
                if at > last_at and (cards != last_cards or at - last_at >= 30):
                    await asyncio.to_thread(persist, db, at, session, cards)
                    last_at, last_cards = at, cards
        except (httpx.HTTPError, TimeoutError, ConnectionError, ValueError, KeyError, TypeError):
            LOG.warning('E17 watch unavailable; reconnecting')
        finally:
            if signals is not None:
                signals.session = None
                signals.changed.set()
            for task in streams:
                task.cancel()
            await asyncio.gather(*streams, return_exceptions=True)
        await asyncio.sleep(2)


def seal_pending(db, cutoff):
    # Recover days missed while the publisher was down. Observations near
    # midnight can contribute to either adjacent date.
    with db.transaction() as conn:
        days = conn.execute("""
            SELECT DISTINCT candidate.day FROM e17.daily_observations o
            CROSS JOIN LATERAL (
                VALUES ((to_timestamp(o.at - %s) AT TIME ZONE 'Asia/Shanghai')::date),
                       ((to_timestamp(o.at + %s) AT TIME ZONE 'Asia/Shanghai')::date)
            ) AS candidate(day)
            LEFT JOIN e17.daily_reports r ON r.day=candidate.day
            WHERE candidate.day <= %s AND r.day IS NULL
            ORDER BY candidate.day
        """, (MAX_GAP, MAX_GAP, cutoff)).fetchall()
    for row in days:
        seal(db, row['day'])


async def run():
    db = Database(os.environ['MAAS_DATABASE_URL'])
    key = Path(os.environ['E17_GPUSTACK_KEY_FILE']).read_text().strip()
    catalog = Path(os.environ['MAAS_CATALOG_FILE'])
    # The session lock prevents multiple instances from interleaving observations.
    with psycopg.connect(db.url, autocommit=True, connect_timeout=5) as lock:
        if not lock.execute('SELECT pg_try_advisory_lock(842125218)').fetchone()[0]:
            raise RuntimeError('An E17 daily collector is already running')
        async with httpx.AsyncClient(base_url=os.environ['MAAS_GPUSTACK_URL'].rstrip('/') + '/v2/',
                                     headers={'Authorization': 'Bearer ' + key},
                                     timeout=httpx.Timeout(30, connect=5)) as client:
            signals = preview = None
            policy_path = os.getenv('MAAS_REPLICA_OBSERVE_POLICY_PATH')
            if policy_path:
                from maas_replica_policy import PolicyFile
                from .replica_observe import run as preview_replicas

                scale_up = None
                if os.getenv('MAAS_REPLICA_ENABLE_APPLY') == '1':
                    from .replica_apply import from_environment
                    scale_up = from_environment()
                    if scale_up is None:
                        raise ValueError('Replica Apply authorization is missing')
                    if scale_up.request.url != os.environ['MAAS_GPUSTACK_URL'].rstrip('/'):
                        raise ValueError('Replica observations and management must use the same GPUStack endpoint')
                logging.basicConfig(level=logging.WARNING)
                logging.getLogger(preview_replicas.__module__).setLevel(logging.INFO)
                signals = SimpleNamespace(session=None, changed=asyncio.Event())
                preview = asyncio.create_task(preview_replicas(
                    client, listing, signals, PolicyFile(Path(policy_path), allow_apply=scale_up is not None),
                    scale_up=scale_up
                ))
            task = asyncio.create_task(observe(db, client, catalog, signals))
            sealed_through = None
            try:
                while not task.done():
                    lock.execute('SELECT 1')
                    try:
                        now = datetime.now(BEIJING)
                        cutoff = now.date() - timedelta(days=1 if now.hour or now.minute >= 10 else 2)
                        if cutoff != sealed_through:
                            await asyncio.to_thread(seal_pending, db, cutoff)
                            sealed_through = cutoff
                    except psycopg.Error:
                        LOG.warning('E17 daily publication database unavailable')
                    await asyncio.wait({task}, timeout=15)
                await task
            finally:
                task.cancel()
                if preview is not None:
                    preview.cancel()
                    await asyncio.gather(preview, return_exceptions=True)
                await asyncio.gather(task, return_exceptions=True)


if __name__ == '__main__':
    asyncio.run(run())
