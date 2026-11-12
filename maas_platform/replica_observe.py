"""MaaS replica planning and explicitly authorized scale-up."""
import asyncio
import logging
import os
from pathlib import Path
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


async def run(client, listing, signals, policy_file, *, interval=10, scale_up=None, writer_factory=None):
    model_watch = None

    def enable_model_watch():
        nonlocal model_watch
        if model_watch is None and hasattr(client, 'stream'):
            model_watch = asyncio.create_task(_watch_models(client, signals.changed))

    try:
        await _run(client, listing, signals, policy_file, interval=interval,
                   scale_up=scale_up, writer_factory=writer_factory, enable_model_watch=enable_model_watch)
    finally:
        if model_watch is not None:
            model_watch.cancel()
            await asyncio.gather(model_watch, return_exceptions=True)


async def _watch_models(client, changed):
    """Wake v2 for external count edits; never alters E17 coverage sessions."""
    delay = 1
    while True:
        try:
            async with client.stream('GET', 'models', params={'watch': 'true'}) as response:
                response.raise_for_status()
                async for line in response.aiter_lines():
                    if line.strip():
                        changed.set()
                        delay = 1
            raise ConnectionError('Model watch ended')
        except Exception:
            # Native periodic full reads remain authoritative. No cached event
            # payload may authorize a write or change E17 observation coverage.
            changed.set()
            await asyncio.sleep(delay)
            delay = min(30, delay * 2)


async def _run(client, listing, signals, policy_file, *, interval, scale_up, writer_factory,
               enable_model_watch):
    pressure = PressureGate.from_environment()
    reconcile = None
    writers = {}
    trusted = scope = session = event = started = last = None
    while True:
        wait_interval = interval
        schema = 1
        before_control = _control_stamp(policy_file, reconcile)
        signals.changed.clear()
        current_session = signals.session
        if session != current_session:
            trusted = event = started = None
            session = current_session
            if reconcile is not None:
                reconcile.signature = reconcile.stable_since = reconcile.view = None
        try:
            policy = policy_file.refresh()
            writer = scale_up
            schema = getattr(policy, 'schema_version', 1)
            if writer_factory is not None:
                if schema not in writers:
                    writers[schema] = writer_factory(policy)
                writer = writers[schema]
            mode = policy.execution.get('mode', 'observe')
            key = (getattr(policy, 'schema_version', 1), policy.cluster, policy.owner, mode)
            if key != scope:
                trusted = event = started = None
                scope = key
            if getattr(policy, 'schema_version', 1) == 2:
                enable_model_watch()
                from .replica_reconcile import ReconcileObserve
                if reconcile is None:
                    state_directory = os.getenv('MAAS_REPLICA_STATE_DIR')
                    if state_directory is None and getattr(writer, 'state', None) is not None:
                        state_directory = writer.state.directory
                    reconcile = ReconcileObserve(native_devices, pressure,
                                                 state_directory=state_directory)
                plan = await reconcile.tick(client, listing, policy, subscribed=current_session is not None,
                                            pending_journal=getattr(writer, 'journal', None))
                wait_interval = policy.reconcile.interval_s
                if plan.get('state') == 'capacity_stabilizing':
                    wait_interval = min(wait_interval, max(0, policy.reconcile.stability_s -
                                                          plan['stable_for_s']))
                if writer is not None and hasattr(writer, 'reconcile'):
                    plan['execution_state'] = await writer.reconcile(policy, reconcile, refresh=policy_file.refresh)
                    plan.pop('executable_preview_limit', None)
                    plan.update(effective_startup_limit=writer.effective_startup_limit,
                                execution_profile=writer.execution_profile,
                                write_attempted=writer.write_attempted,
                                writes_enabled=(mode == 'apply' and plan.get('ownership') == 'adopted'
                                                and writer.authorized(policy) is None))
                    if reconcile.state:
                        from .replica_state import atomic_private
                        atomic_private(reconcile.state.directory/'status.json', plan)
                    if plan['execution_state'] == 'confirmed':
                        # Confirmed native writes do not need another arrival or
                        # the full fallback period to re-evaluate spare slots.
                        wait_interval = 0
                plan['next_check_in_s'] = wait_interval + 1
                # Never pass v2 into the arrival-only writer.
            elif current_session is None:
                raise ConnectionError('waiting_for_subscription')
            else:
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
                if writer is not None:
                    from .replica_apply import deployment_binding

                    def arrival_valid():
                        return (signals.session == current_session
                                and started is not None
                                and monotonic() - started < policy.arrival_window_s)

                    bindings = ({ident: deployment_binding(models[ident]) for ident in policy.models}
                                if mode == 'apply' and plan.get('up_preview_limit') else {})
                    plan['execution_state'] = await writer.advance(
                        policy, event, devices, plan, refresh=policy_file.refresh,
                        bindings=bindings, arrival_valid=arrival_valid,
                    )
        except Exception as exc:
            trusted = event = started = None
            if reconcile is not None:
                reconcile.signature = reconcile.stable_since = None
                reconcile.view = None
            plan = {'state': 'blocked', 'reason': type(exc).__name__}
        comparison = _log_identity(plan)
        if comparison != last:
            LOG.info('MaaS replica observe %s', plan)
            last = comparison
        await _wait(signals.changed, wait_interval, policy_file, reconcile,
                    before_control if schema == 2 else None)
        await asyncio.sleep(1)


def _control_stamp(policy_file, reconcile):
    paths = [getattr(policy_file, 'path', None)]
    if reconcile is not None and reconcile.state is not None:
        paths.append(reconcile.state.path)
    result = []
    for path in paths:
        if path is None:
            continue
        try:
            value = Path(path).stat()
            result.append((value.st_ino, value.st_mtime_ns, value.st_size))
        except OSError:
            result.append(None)
    return tuple(result)


async def _wait(changed, timeout, policy_file, reconcile, control_stamp):
    """Cheap local file checks wake cross-container pause/resume within a second.

    They do not poll GPUStack more frequently when the controls are unchanged.
    """
    deadline = monotonic() + timeout
    while not changed.is_set():
        if control_stamp is not None and _control_stamp(policy_file, reconcile) != control_stamp:
            return
        remaining = deadline - monotonic()
        if remaining <= 0:
            return
        try:
            await asyncio.wait_for(changed.wait(), timeout=min(1, remaining))
        except TimeoutError:
            pass


def _log_identity(value):
    """Do not emit identical state transitions on every timestamp/age change."""
    if isinstance(value, dict):
        return {key: _log_identity(item) for key, item in value.items()
                if key not in {'observed_at', 'stable_for_s', 'sample_age_s', 'snapshot_age_s',
                               'next_check_in_s', 'observed_for_s'}}
    if isinstance(value, list):
        return [_log_identity(item) for item in value]
    return value
