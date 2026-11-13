"""Bounded private cold-trial observer using the production subscription/reconcile loop."""
import argparse
import asyncio
import hashlib
import json
import logging
import os
from pathlib import Path
from time import time
from types import SimpleNamespace

import httpx

from maas_platform import gpu_daily_watch as daily
from maas_platform.replica_cold_trial import ColdRequest, WarmRequest
from maas_platform.replica_contract import ControlPolicyFile, private_json
from maas_platform.replica_observe import run
from maas_platform.replica_state import atomic_private
from maas_platform.replica_writer_v2 import ReconcileWriter


def append(path, kind, value):
    with path.open('a') as handle:
        os.chmod(path, 0o600)
        handle.write(json.dumps({'at': time(), 'kind': kind, 'value': value})+'\n')


async def session(directory, duration, mode):
    spec = private_json(directory/'spec.json')
    writer = ReconcileWriter.from_environment()
    guard = WarmRequest if mode == 'warm' else ColdRequest
    writer.request = guard(writer.request, spec, directory, allow_start=mode in ('cold', 'warm'))
    loader = ControlPolicyFile(directory/'policy.json', allow_apply=True)
    signatures = {}
    original_listing = daily.listing

    async def listing(client, kind):
        rows = await original_listing(client, kind)
        if kind == 'workers':
            shape = [{'id': r['id'], 'state': r['state'], 'name': r['name'],
                'cluster': r['cluster_id'], 'maintenance': r.get('maintenance'),
                'gpu': [{'uuid': g.get('uuid'), 'memory': g.get('memory')} for g in
                    (r.get('status') or {}).get('gpu_devices') or []]} for r in rows.values()]
        elif kind == 'models':
            shape = [{'id': r['id'], 'target': r['replicas'], 'ready': r['ready_replicas'],
                      'selector': r.get('worker_selector')} for r in rows.values()]
        else:
            shape = [{'id': r['id'], 'model': r['model_id'], 'state': r['state'],
                'message': r.get('state_message'), 'worker': r.get('worker_id'),
                'gpu': r.get('gpu_indexes'), 'claim': r.get('computed_resource_claim')}
                     for r in rows.values()]
        # Only transitions plus latest full lists: no unbounded per-second raw history.
        shape.sort(key=lambda row: row['id'])
        text = json.dumps(shape, sort_keys=True); signature = hashlib.sha256(text.encode()).hexdigest()
        atomic_private(directory/(kind+'-latest-private.json'), {'at': time(), 'items': shape})
        if signatures.get(kind) != signature:
            append(directory/'transitions-private.jsonl', kind, shape); signatures[kind] = signature
        return rows

    daily.listing = listing
    daily.persist = lambda _db, at, sid, cards: atomic_private(directory/'subscription-private.json',
                                {'at': at, 'session': str(sid), 'cards': cards})
    key = Path(os.environ['MAAS_REPLICA_MANAGEMENT_KEY_FILE']).read_text().strip()
    signals = SimpleNamespace(session=None, changed=asyncio.Event())
    atomic_private(directory/'observer-session-private.json', {'started_at': time(),
        'mode': mode, 'duration_s': duration, 'automatic_release': False, 'phase': 'RUNNING'})
    async with httpx.AsyncClient(base_url=writer.request.url+'/v2/', trust_env=False,
            headers={'Authorization': 'Bearer '+key}, timeout=httpx.Timeout(30, connect=5)) as client:
        tasks = [asyncio.create_task(daily.observe(None, client, Path(os.environ['MAAS_CATALOG_FILE']), signals)),
                 asyncio.create_task(run(client, listing, signals, loader, writer_factory=lambda _: writer))]
        try:
            await asyncio.wait_for(asyncio.gather(*tasks), timeout=duration)
        except TimeoutError:
            atomic_private(directory/'observer-session-private.json', {'ended_at': time(),
                'mode': mode, 'duration_s': duration, 'phase': 'WINDOW_ENDED',
                'automatic_release': False, 'pending_write': writer.journal.read() is not None})
        finally:
            for task in tasks:task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--directory', type=Path, required=True)
    p.add_argument('--duration', type=int, default=7200)
    p.add_argument('--mode', choices=('observe', 'cold', 'warm'), default='observe')
    args = p.parse_args()
    if not args.directory.is_absolute() or not 60 <= args.duration <= 7200:
        p.error('Absolute private directory and bounded window 60..7200 seconds required')
    logging.basicConfig(level=logging.WARNING)
    from maas_platform.gpu_daily_watch import run_service
    asyncio.run(run_service(lambda: session(args.directory, args.duration, args.mode)))


if __name__ == '__main__':main()
