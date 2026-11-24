"""Issue #75. Capture a settled GPUStack inventory with GETs; replay it without an upstream.

In the controller environment: python replica_retry_replay.py capture --model-id ID
Pipe stdout to: python replica_retry_replay.py replay (same image, network=none, private /tmp).
The selected model needs 2–4 Ready replicas across multiple workers, without selectors.
"""
import argparse
import asyncio
from collections import Counter
import hashlib
from http.server import BaseHTTPRequestHandler, HTTPServer
import json
import os
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
from threading import Thread
from urllib.parse import urlsplit

from maas_replica_apply import LocalManagement
from maas_replica_execution import PendingWrite
from maas_replica_pressure import PressureGate
from maas_platform.replica_apply import MODEL_UPDATE_FIELDS, model_settings
from maas_platform.replica_contract import ControlPolicy
from maas_platform.replica_observe import native_devices
from maas_platform.replica_reconcile import ReconcileObserve
from maas_platform.replica_trial import inventory, pilot_policy
from maas_platform.replica_writer_v2 import ReconcileWriter


def require(condition, message):
    if not condition:
        raise ValueError(message)


async def capture(model_id):
    source = LocalManagement(os.environ['MAAS_REPLICA_MANAGEMENT_URL'],
                             os.environ['MAAS_REPLICA_MANAGEMENT_KEY_FILE'])
    require((await source('GET', '/version'))['version'] == 'v2.2.3', 'Unverified native version')
    workers, models, instances = await asyncio.gather(
        *(inventory(source, kind) for kind in ('workers', 'models', 'model-instances')))
    cluster = models[model_id]['cluster_id']
    devices = await native_devices(cluster, workers)
    require(devices, 'No verified GPU inventory')
    cards = {}
    for uuid, card in devices.items():
        row = cards.setdefault(card.worker_id, dict(
            id=card.worker_id, state='ready', unreachable=False, labels={}, cluster_id=cluster,
            system_reserved={'vram': card.reserved_vram}, status={'gpu_devices': []}))
        row['status']['gpu_devices'].append(dict(
            index=card.index, type=card.kind, uuid=hashlib.sha256(uuid.encode()).hexdigest(),
            memory=dict(total=card.total_vram, allocated=card.allocated_vram)))
    selected = {}
    for key, row in models.items():
        if row['cluster_id'] != cluster:
            continue
        require(row['ready_replicas'] == row['replicas'], 'Capture needs settled deployments')
        require(not any(row.get(k) for k in ('gpu_selector', 'worker_selector',
                        'distributed_inference_across_workers', 'cpu_offloading')), 'Unsupported placement')
        item = dict.fromkeys(MODEL_UPDATE_FIELDS)
        item.update({k: row[k] for k in ('id', 'cluster_id', 'owner_principal_id', 'replicas',
                                          'ready_replicas', 'placement_strategy')})
        item.update(name=f'test_agent_model_{key}', env={'GPUSTACK_MODEL_VRAM_CLAIM':
                                                         row['env']['GPUSTACK_MODEL_VRAM_CLAIM']})
        selected[key] = item
    placements = []
    for row in instances.values():
        if row['model_id'] in selected:
            require(row['state'] == 'running' and row['worker_id'] in cards and len(row['gpu_indexes']) == 1
                    and not row.get('deleted_at') and not (row.get('distributed_servers') or {}).get('subordinate_workers'),
                    'Incomplete or unsettled GPU placement')
            item = {k: row[k] for k in ('id', 'model_id', 'state', 'worker_id', 'gpu_indexes')}
            item['computed_resource_claim'] = {'vram': row['computed_resource_claim']['vram']}
            placements.append(item)
    require(all(sum(i['model_id'] == key for i in placements) == row['replicas']
                for key, row in selected.items()), 'Instance count changed during capture')
    return dict(model_id=model_id, workers=list(cards.values()), models=list(selected.values()),
                **{'model-instances': placements})


class ReplayServer(HTTPServer):
    """Local native API double. The only accepted write is the regulator's own decrement."""
    def __init__(self, data):
        super().__init__(('127.0.0.1', 0), ReplayHandler)
        self.data = data
        self.model = next(m for m in data['models'] if m['id'] == data['model_id'])
        self.model_path = f"/v2/models/{data['model_id']}"
        self.fail_preflight = False
        self.failed_reads = self.gets = self.puts = 0
        self.accepted = None


class ReplayHandler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def reply(self, body):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(json.dumps(body).encode())

    def do_GET(self):
        peer = self.server
        path = urlsplit(self.path).path
        peer.gets += 1
        if path == peer.model_path and peer.fail_preflight:
            peer.fail_preflight = False
            peer.failed_reads += 1
            self.send_error(503)
        elif path == peer.model_path:
            self.reply(peer.model)
        elif path == '/version':
            self.reply({'version': 'v2.2.3'})
        elif path in ('/v2/workers', '/v2/models', '/v2/model-instances'):
            items = peer.data[path.split('/')[-1]]
            self.reply(dict(items=items, pagination=dict(page=1, totalPage=1, total=len(items))))
        else:
            self.send_error(404)

    def do_PUT(self):
        peer = self.server
        peer.puts += 1
        body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
        expected = {**model_settings(peer.model), 'replicas': peer.model['replicas'] - 1}
        if self.path != peer.model_path or peer.accepted is not None or body != expected:
            self.send_error(409)
            return
        peer.accepted = body
        # Consume the entire request, then close the connection without any HTTP response.
        self.close_connection = True


async def exercise(peer, root):
    model = peer.model
    ident, initial = model['id'], model['replicas']
    require(2 <= initial <= 4, 'Expected 2–4 Ready replicas')
    target_rows = [r for r in peer.data['model-instances'] if r['model_id'] == ident]
    placement = Counter(r['worker_id'] for r in target_rows)
    require(len(placement) >= 2, 'Need two occupied workers')
    card_host = next(w for w in peer.data['workers'] if w['id'] == min(placement, key=placement.get))
    policy = ControlPolicy(pilot_policy(model, maximum=initial, limit=1, pressure_mode='disabled',
                                       parallel=False, stability=0), allow_apply=True)
    key = root/'test-key'
    key.write_text('test-only')
    key.chmod(0o600)
    client = LocalManagement(f'http://127.0.0.1:{peer.server_port}', key)

    def controller():
        observer = ReconcileObserve(native_devices, PressureGate(None), state_directory=root/'state')
        writer = ReconcileWriter(model['cluster_id'], {ident: initial}, PendingWrite(root/'write.json'),
                                 observer.state, client)
        return observer, writer
    observer, writer = controller()
    observer.state.adopt(policy, {ident: model}, expected_targets={ident: initial})

    async def listing(_, kind):
        return await inventory(client, kind)

    blocked_recovery_plans = 0
    independent_readbacks = []

    async def tick(expected):
        nonlocal blocked_recovery_plans
        recovering = writer.journal.read() is not None
        plan = await observer.tick(None, listing, policy, subscribed=False, pending_journal=writer.journal)
        if not plan.get('snapshot_complete'):
            require(recovering and expected in ('unresolved', 'confirmed'),
                    f"Incomplete replay observation: {plan.get('reason')}")
            blocked_recovery_plans += 1
        # Production calls reconcile even when planning is blocked: an outstanding write
        # must get its independent native readback before any plan can dispatch.
        reads_before = peer.gets
        result = await writer.reconcile(policy, observer, refresh=lambda: policy)
        if recovering:
            independent_readbacks.append(peer.gets - reads_before)
            require(independent_readbacks[-1] >= 4, 'Missing version and full native readback')
        require(result == expected, f'Expected {expected}, got {result}')
        return plan

    async def failed_read():
        peer.fail_preflight = True
        plan = await tick('downscale_retry_scheduled')
        require(plan['downscale_retry']['reason'] == 'downscale_preflight_unavailable'
                and plan['policy_target'][ident] < initial and not peer.puts and not writer.journal.read(),
                'Preflight failure did not prove zero PUTs')
        await tick('downscale_retry_backoff')
        await asyncio.sleep(plan['downscale_retry']['delay_s'] + .1)
        return plan['policy_target'][ident]

    # Replay one worker becoming unavailable and then available during backoff.
    card_host['state'] = 'not_ready'
    before = await failed_read()
    card_host['state'] = 'ready'
    after = await tick('idle')
    require(after['policy_target'][ident] == initial and not peer.puts, 'Obsolete shrink was replayed')

    # With the shortage still present at retry, the peer receives exactly one PUT.
    card_host['state'] = 'not_ready'
    await failed_read()
    await tick('unresolved')
    require(peer.puts == 1 and peer.accepted and writer.journal.read(), 'Missing uncertain write')
    observer, writer = controller()
    for _ in range(3):
        await tick('unresolved')
        require(peer.puts == 1 and writer.journal.read(), 'Uncertain write was cleared or resent')
    # Publish the peer's already accepted operation and model native deletion in the copy only.
    victim = next(r for r in target_rows if r['worker_id'] == card_host['id'])
    peer.data['model-instances'].remove(victim)
    model.update(replicas=peer.accepted['replicas'], ready_replicas=initial-1)
    await tick('confirmed')
    require(peer.puts == 1 and writer.journal.read() is None, 'Expected single confirmed PUT')
    return dict(workers=len(peer.data['workers']), initial_replicas=initial, shortage_target=before,
                recovered_target=after['policy_target'][ident], recovered_resource_puts=0,
                failed_preflights=peer.failed_reads, persistent_shortage_puts=peer.puts,
                confirmed_replicas=model['replicas'], old_value_checks=3, recovery_gets=independent_readbacks,
                reconstructed_writer=True, blocked_recovery_plans=blocked_recovery_plans,
                journal_cleared_after_confirmation=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    modes = parser.add_subparsers(dest='mode', required=True)
    modes.add_parser('capture').add_argument('--model-id', type=int, required=True)
    modes.add_parser('replay')
    args = parser.parse_args()
    if args.mode == 'capture':
        print(json.dumps(asyncio.run(capture(args.model_id))))
        return
    with TemporaryDirectory(prefix='tmp_agent_retry_') as temp, ReplayServer(json.load(sys.stdin)) as peer:
        thread = Thread(target=peer.serve_forever, daemon=True)
        thread.start()
        try:
            print(json.dumps(asyncio.run(exercise(peer, Path(temp)))))
        finally:
            peer.shutdown()
            thread.join()


if __name__ == '__main__':
    main()
