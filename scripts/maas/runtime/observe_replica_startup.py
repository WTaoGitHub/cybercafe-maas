"""Read the live replica timeline in an existing MaaS controller container."""

import argparse
import asyncio
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import time

from maas_replica_apply import LocalManagement
from maas_replica_execution import PendingWrite
from maas_replica_native import NativeAdapter
from maas_platform.replica_contract import private_json
from maas_platform.replica_state import ControlState


def fields(row, keys):
    return {key: row[key] for key in keys.split() if key in row}


def controller_evidence(status, model_ids, pending):
    view = fields(status, 'observed_at revision snapshot_complete eligible_worker_ids '
                  'policy_target effective_startup_limit global_inflight cluster_inflight '
                  'startup_reservation pressure_mode execution_state writes_enabled '
                  'state reason ownership write_attempted')
    decisions = status.get('pending_control', {})
    view['pending_control'] = {
        str(i): fields(decisions[str(i)], 'state observed_for_s target qualified')
        for i in model_ids if str(i) in decisions
    }
    retry = status.get('downscale_retry', {})
    if retry.get('model_id') in model_ids:
        view['downscale_retry'] = fields(retry, 'model_id reason failures delay_s')
    if pending is not None and pending.model_id in model_ids:
        view['pending_action'] = {'model_id': pending.model_id, 'before': pending.before,
                                  'after': pending.after}
    return view


async def observe(args):
    cluster = int(os.environ['MAAS_REPLICA_APPLY_CLUSTER'])
    authorized = set(map(int, json.loads(os.environ['MAAS_REPLICA_APPLY_MODEL_CAPS'])))
    model_ids = set(args.model or authorized)
    if not model_ids <= authorized:
        raise ValueError('Model outside the controller scope')
    journal = PendingWrite(os.environ['MAAS_REPLICA_WRITE_JOURNAL'])
    state = ControlState(os.getenv('MAAS_REPLICA_STATE_DIR', str(journal.path.parent / 'state')))
    request = LocalManagement(os.environ['MAAS_REPLICA_MANAGEMENT_URL'],
                              os.environ['MAAS_REPLICA_MANAGEMENT_KEY_FILE'])
    native = NativeAdapter(request, revision=0, cluster_id=cluster, owner_id=0, model_ids=model_ids)
    failures = 0
    for index in range(args.samples):
        started = time.monotonic()
        sample = {'at_utc': datetime.now(timezone.utc).isoformat(timespec='milliseconds')}
        try:
            async with asyncio.timeout(8):
                workers, models, instances = await asyncio.gather(*(
                    native._pages(f'/v2/{name}') for name in ('workers', 'models', 'model-instances')))
            selected = [r for r in models if r['id'] in model_ids and r['cluster_id'] == cluster]
            if {r['id'] for r in selected} != model_ids:
                raise ValueError('Expected deployment missing')
            status = private_json(state.directory / 'status.json')
            owner = state.read() or {}
            pending = journal.read()
            sample.update(
                workers=[fields(r, 'id state unreachable updated_at') for r in workers
                         if r['cluster_id'] == cluster],
                models=[fields(r, 'id replicas ready_replicas updated_at') for r in selected],
                instances=[fields(r, 'id model_id worker_id state created_at updated_at')
                           for r in instances if r['model_id'] in model_ids],
                controller=controller_evidence(status, model_ids, pending),
                ownership=fields(owner, 'generation paused last_write_at'),
                pending_write=pending is not None,
            )
            failures = 0
        except (OSError, ValueError, KeyError, TypeError) as exc:
            sample['unavailable'] = type(exc).__name__
            failures += 1
        sample['read_seconds'] = round(time.monotonic() - started, 3)
        print(json.dumps(sample, separators=(',', ':')), flush=True)
        if failures >= 2:
            return 2
        if index + 1 < args.samples:
            await asyncio.sleep(max(0, args.interval - (time.monotonic() - started)))
    return int(bool(failures))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--model', type=int, action='append', help='Limit to these authorized deployment IDs')
    parser.add_argument('--samples', type=int, default=3)
    parser.add_argument('--interval', type=int, default=5, help='Sampling interval in seconds')
    args = parser.parse_args()
    if not 1 <= args.samples <= 720 or not 2 <= args.interval <= 60:
        parser.error('Require 1–720 samples and a 2–60 second interval')
    try:
        return asyncio.run(observe(args))
    except (OSError, ValueError, KeyError, TypeError) as exc:
        parser.exit(2, f'Controller observation unavailable ({type(exc).__name__})\n')


if __name__ == '__main__':
    raise SystemExit(main())
