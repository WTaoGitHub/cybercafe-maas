"""Read-only worker capacity reconciliation for GPUStack v2.2.3."""
import asyncio
import logging
import os
from pathlib import Path
import time
from dataclasses import dataclass

from gpustack.extension import Plugin
from gpustack.schemas.clusters import Cluster
from gpustack.schemas.models import Model, ModelInstance
from gpustack.schemas.workers import Worker
from gpustack.server.db import async_session
from gpustack.server.worker_allocated_cache import get_worker_allocated

from maas_replica_apply import ScaleUp
from maas_replica_native import binding
from maas_replica_policy import ARRIVAL_EVENTS, PolicyFile, arrival_context, solve, summarize
from maas_replica_pressure import PressureGate

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class Device:
    worker_id: int
    index: int
    free_vram: int
    total_vram: int
    allocated_vram: int
    reserved_vram: int
    labels: tuple
    kind: str


async def snapshot_devices(cluster_ids, workers, allocated=get_worker_allocated):
    clusters = {ident: {} for ident in cluster_ids}
    unknown = set()
    owners = {}
    for worker in workers:
        cluster = worker.cluster_id
        if cluster not in clusters or worker.state != "ready" or worker.unreachable:
            continue
        if worker.maintenance and worker.maintenance.enabled:
            continue
        if worker.status is None or worker.status.gpu_devices is None:
            unknown.add(cluster)
            continue
        if not worker.status.gpu_devices:
            continue
        reserved = getattr(worker.system_reserved, "vram", None)
        usage = await allocated(worker.id)
        indexes = set()
        for device in worker.status.gpu_devices:
            uuid, index = device.uuid, device.index
            if not uuid:
                unknown.add(cluster)
                continue
            if uuid in owners:
                unknown.update((cluster, owners[uuid]))
            owners[uuid] = cluster
            total = getattr(device.memory, "total", None)
            used = usage.vram.get(index, 0)
            if (index in indexes or any(type(value) is not int or value < 0
                                       for value in (index, total, used, reserved))):
                unknown.add(cluster)
                continue
            indexes.add(index)
            clusters[cluster][uuid] = Device(
                worker.id, index, max(0, total - used - reserved), total, used, reserved,
                tuple(sorted((worker.labels or {}).items())), device.type)
    return {ident: None if ident in unknown else devices for ident, devices in clusters.items()}


async def read_capacity():
    async with async_session() as session:
        clusters = await Cluster.all_by_fields(session, {"deleted_at": None})
        workers = await Worker.all_by_fields(session, {"deleted_at": None})
    return await snapshot_devices((cluster.id for cluster in clusters), workers)


async def read_deployments():
    async with async_session() as session:
        models = await Model.all_by_fields(session, {"deleted_at": None})
        instances = await ModelInstance.all_by_fields(session, {"deleted_at": None})
    return models, instances


def plan_snapshot(policy, event, devices, model_rows, instance_rows):
    if event not in ARRIVAL_EVENTS:
        raise ValueError("Phase one requires a confirmed GPU arrival")
    rules, execution = policy.effective(event)
    models = {model.id: model for model in model_rows if model.id in rules}
    if models.keys() != rules.keys():
        raise ValueError("Managed deployment missing from snapshot")
    if any(m.cluster_id != policy.cluster or m.owner_principal_id != policy.owner
           for m in models.values()):
        raise ValueError("Managed deployment scope mismatch")
    cards = sorted(devices.values(), key=lambda card: (card.worker_id, card.index))
    indexes = {(card.worker_id, card.index): i for i, card in enumerate(cards)}
    eligible = {}
    for key, model in models.items():
        if (model.cpu_offloading or model.distributed_inference_across_workers
                or model.gpu_selector or getattr(model, "gpu_type_selector", None)
                or str((model.env or {}).get("GPUSTACK_MODEL_VRAM_CLAIM"))
                != str(rules[key].vram_claim_bytes)):
            raise ValueError("Unsupported placement or GPU claim mismatch")
        selector = model.worker_selector or {}
        eligible[key] = [i for i, card in enumerate(cards)
                         if card.kind == "cuda" and all(dict(card.labels).get(k) == v
                                                        for k, v in selector.items())]
    running = {}
    for instance in instance_rows:
        if instance.state == "running" and instance.gpu_indexes and all(
            (instance.worker_id, index) in indexes for index in instance.gpu_indexes
        ):
            running[instance.model_id] = running.get(instance.model_id, 0) + 1
    current = {key: model.replicas for key, model in models.items()}
    # Keep all current allocations in place. Native pending replicas have
    # first use of the new capacity before we request any additional slots.
    if any(running.get(key, 0) < count for key, count in current.items()):
        target = current.copy()
        waiting = True
    else:
        target = solve(rules, [card.free_vram for card in cards], eligible, current,
                       execution.get("solve_timeout_s", 1), additive=True)
        waiting = False
    global_inflight = sum(max(0, model.replicas - model.ready_replicas) for model in model_rows)
    cluster_inflight = sum(max(0, model.replicas - running.get(model.id, 0))
                           for model in model_rows if model.cluster_id == policy.cluster)
    preview = summarize(rules, execution, target, current, global_inflight, cluster_inflight)
    return {"revision": policy.revision, "event": event, "cluster": policy.cluster,
            "global_inflight": global_inflight, "cluster_inflight": cluster_inflight,
            "waiting_for_managed_instances": waiting, **preview}


async def observe_workers(changed):
    while True:
        try:
            async for event in Worker.subscribe(source="maas-replica", replay_existing=False):
                if event.data is not None:
                    changed.set()
            raise RuntimeError("Worker subscription closed")
        except Exception:
            log.exception("Replica worker subscription failed")
            changed.set()
            await asyncio.sleep(2)


async def reconcile(changed, read=read_capacity, *, interval=10, zero_grace=10,
                    policy_file=None, scale_up=None, observe_pressure=None, clock=time.monotonic):
    if observe_pressure is None:
        observe_pressure = PressureGate(None)
    zero_since = {}
    previous = {}
    trusted = {}
    event = None
    event_started = None
    last_plan = None
    scope = None
    rejected = None
    last_execution = None
    while True:
        changed.clear()
        try:
            snapshot = await asyncio.wait_for(read(), timeout=5)
        except Exception as error:
            snapshot = dict.fromkeys(previous)
            log.warning("Replica capacity snapshot unavailable: %s", type(error).__name__)
        now = clock()
        for cluster in previous.keys() - snapshot.keys():
            zero_since.pop(cluster, None)
            trusted.pop(cluster, None)
        current = {}
        policy = None
        if policy_file is not None:
            try:
                policy = policy_file.refresh()
                rejected = None
            except (OSError, ValueError, TypeError, KeyError, AttributeError) as error:
                failure = type(error).__name__
                if rejected != failure:
                    log.warning("Replica policy rejected: %s", failure)
                    rejected = failure
                policy = policy_file.active
        if policy is not None:
            active_scope = policy.cluster, policy.owner, policy.execution.get("mode", "observe")
            if scope != active_scope:
                scope = active_scope
                event = event_started = None
        for cluster, devices in snapshot.items():
            if devices is None:
                state = "unknown"
                zero_since.pop(cluster, None)
            elif devices:
                state = "available"
                zero_since.pop(cluster, None)
            else:
                since = zero_since.setdefault(cluster, now)
                state = "zero" if now - since >= zero_grace else "zero_pending"

            old = trusted.get(cluster, {})
            added = removed = moved = 0
            if state in ("available", "zero"):
                if policy is not None and policy.cluster == cluster:
                    before = ({(key, d.worker_id, d.index) for key, d in old.items()}
                              if cluster in trusted else None)
                    after = {(key, d.worker_id, d.index) for key, d in devices.items()}
                    event = arrival_context(before, after, policy, event)
                    if event is None:
                        event_started = None
                    elif before != after:
                        event_started = now
                    if event_started is not None and now - event_started >= policy.arrival_window_s:
                        event = event_started = None
                added = len(devices.keys() - old.keys())
                removed = len(old.keys() - devices.keys())
                moved = sum((devices[key].worker_id, devices[key].index)
                            != (old[key].worker_id, old[key].index)
                            for key in devices.keys() & old.keys())
                trusted[cluster] = devices
            current[cluster] = state, devices
            if current[cluster] != previous.get(cluster):
                count = None if devices is None else len(devices)
                free = None if devices is None else sum(d.free_vram for d in devices.values())
                log.info("Replica capacity cluster=%s state=%s devices=%s free_vram=%s "
                         "added=%s removed=%s moved=%s", cluster, state, count, free,
                         added, removed, moved)
        if not current and previous:
            log.info("Replica capacity has no clusters")
        previous = current
        if policy is not None:
            bindings = {}
            state, devices = current.get(policy.cluster, ("unknown", None))
            if state == "zero_pending" or devices is None:
                event = None
                plan = {"revision": policy.revision, "cluster": policy.cluster, "state": state}
            elif not devices:
                plan = {"revision": policy.revision, "cluster": policy.cluster,
                        "state": "waiting_for_capacity"}
            elif event not in ARRIVAL_EVENTS:
                plan = {"revision": policy.revision, "cluster": policy.cluster,
                        "state": "waiting_for_gpu_arrival"}
            else:
                try:
                    rows = await asyncio.wait_for(read_deployments(), timeout=5)
                    plan = await asyncio.to_thread(plan_snapshot, policy, event, devices, *rows)
                    if scale_up is not None and plan["up_preview_limit"]:
                        bindings = {m.id: binding(m.model_dump(mode="json")) for m in rows[0]
                                    if m.id in policy.models}
                except Exception as error:
                    plan = {"revision": policy.revision, "cluster": policy.cluster,
                            "event": event, "blocked": str(error) if type(error) is ValueError
                            else type(error).__name__}
            if policy.execution.get("mode", "observe") == "observe":
                pressure_state = observe_pressure.evaluate(policy.pressure)
                plan["pressure_state"] = pressure_state
                plan["pressure_up_preview_limit"] = (
                    plan.get("up_preview_limit", 0) if pressure_state == "open" else 0
                )
            else:
                observe_pressure.evaluate(None)
            if plan != last_plan:
                log.info("Replica observe plan %s", plan)
                last_plan = plan
            if scale_up is not None and rejected is None:
                try:
                    def arrival_valid():
                        return (event_started is not None
                                and clock() - event_started < policy.arrival_window_s)

                    execution = await scale_up.advance(policy, event, devices, plan,
                                                       refresh=policy_file.refresh, bindings=bindings,
                                                       arrival_valid=arrival_valid)
                except Exception as error:
                    execution = type(error).__name__
                if execution != last_execution:
                    log.info("Replica scale-up execution state=%s", execution)
                    last_execution = execution
        try:
            await asyncio.wait_for(changed.wait(), timeout=interval)
        except TimeoutError:
            pass
        await asyncio.sleep(1)


class ReplicaPlugin(Plugin):
    def async_tasks(self):
        changed = asyncio.Event()
        path = os.environ.get("MAAS_REPLICA_POLICY_PATH")
        try:
            scale_up = ScaleUp.from_environment() if path else None
        except (OSError, ValueError, KeyError) as error:
            log.warning("Replica apply configuration rejected: %s", type(error).__name__)
            scale_up = None
        policy_file = PolicyFile(Path(path), allow_apply=scale_up is not None) if path else None
        try:
            observe_pressure = PressureGate.from_environment()
        except ValueError:
            log.warning("Replica observe pressure path rejected")
            observe_pressure = PressureGate(None)
        return [observe_workers(changed), reconcile(changed, policy_file=policy_file,
                                                  scale_up=scale_up, observe_pressure=observe_pressure)]
