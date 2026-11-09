"""Read-only MaaS counterpart of the GPUStack replica regulator."""
import asyncio
import logging
from types import SimpleNamespace
from time import monotonic

from maas_replica_planner import plan_snapshot, snapshot_devices
from maas_replica_policy import ARRIVAL_EVENTS, arrival_context
from maas_replica_pressure import PressureGate

LOG = logging.getLogger(__name__)


async def native_devices(cluster, workers):
    """Normalize the public worker list using the native allocation fields."""
    normalized = []
    allocations = {}
    for worker in workers.values():
        if worker['cluster_id'] != cluster:
            continue
        row = dict(worker)
        row['maintenance'] = SimpleNamespace(**(row.get('maintenance') or {'enabled': False}))
        row['system_reserved'] = SimpleNamespace(**(row.get('system_reserved') or {}))
        status = row.get('status')
        if status is not None:
            devices = status.get('gpu_devices')
            if devices is not None:
                allocations[row['id']] = SimpleNamespace(vram={
                    gpu['index']: (gpu.get('memory') or {}).get('allocated') for gpu in devices
                })
                devices = [
                    SimpleNamespace(**{**gpu, 'memory': SimpleNamespace(**(gpu.get('memory') or {}))})
                    for gpu in devices
                ]
            row['status'] = SimpleNamespace(gpu_devices=devices)
        normalized.append(SimpleNamespace(**row))

    async def allocated(worker_id):
        return allocations[worker_id]

    return (await snapshot_devices((cluster,), normalized, allocated))[cluster]


async def run(client, listing, signals, policy_file, *, interval=10):
    pressure = PressureGate.from_environment()
    trusted = scope = session = event = started = last = None
    while True:
        signals.changed.clear()
        current_session = signals.session
        if session != current_session:
            trusted = event = started = None
            session = current_session
        try:
            policy = policy_file.refresh()
            key = (policy.cluster, policy.owner)
            if key != scope:
                trusted = event = started = None
                scope = key
            if current_session is None:
                raise ConnectionError('waiting_for_subscription')
            async with asyncio.timeout(5):
                workers, models, instances = await asyncio.gather(
                    *(listing(client, name) for name in ('workers', 'models', 'model-instances'))
                )
                devices = await native_devices(policy.cluster, workers)
            if signals.session != current_session or devices is None:
                raise ValueError('untrusted_capacity')
            after = {(uuid, card.worker_id, card.index) for uuid, card in devices.items()}
            next_event = arrival_context(trusted, after, policy, event)
            now = monotonic()
            if next_event is None:
                started = None
            elif trusted != after:
                started = now
            event, trusted = next_event, after
            if started is not None and now - started >= policy.arrival_window_s:
                event = started = None
            if event in ARRIVAL_EVENTS and devices:
                plan = await asyncio.to_thread(
                    plan_snapshot, policy, event, devices,
                    [SimpleNamespace(**model) for model in models.values()],
                    [SimpleNamespace(**instance) for instance in instances.values()],
                )
            else:
                plan = {'state': 'waiting_for_gpu_arrival'}
            worker_ids = ({card.worker_id for card in devices.values()}
                          if policy.pressure and policy.pressure.require_worker_coverage else None)
            state = pressure.evaluate(policy.pressure, worker_ids=worker_ids)
            plan.update(pressure_state=state,
                        pressure_up_preview_limit=plan.get('up_preview_limit', 0) if state == 'open' else 0)
            if signals.session != current_session:
                raise ConnectionError('subscription_changed')
        except Exception as exc:
            trusted = event = started = None
            plan = {'state': 'blocked', 'reason': type(exc).__name__}
        if plan != last:
            LOG.info('MaaS replica observe %s', plan)
            last = plan
        try:
            await asyncio.wait_for(signals.changed.wait(), timeout=interval)
        except TimeoutError:
            pass
        await asyncio.sleep(1)
