"""Read-only worker capacity reconciliation for GPUStack v2.2.3."""
import asyncio
import logging
import time
from dataclasses import dataclass

from gpustack.extension import Plugin
from gpustack.schemas.clusters import Cluster
from gpustack.schemas.workers import Worker
from gpustack.server.db import async_session
from gpustack.server.worker_allocated_cache import get_worker_allocated

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class Device:
    worker_id: int
    index: int
    free_vram: int


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
            clusters[cluster][uuid] = Device(worker.id, index, max(0, total - used - reserved))
    return {ident: None if ident in unknown else devices for ident, devices in clusters.items()}


async def read_capacity():
    async with async_session() as session:
        clusters = await Cluster.all_by_fields(session, {"deleted_at": None})
        workers = await Worker.all_by_fields(session, {"deleted_at": None})
    return await snapshot_devices((cluster.id for cluster in clusters), workers)


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


async def reconcile(changed, read=read_capacity, *, interval=10, zero_grace=10):
    zero_since = {}
    previous = {}
    trusted = {}
    while True:
        changed.clear()
        try:
            snapshot = await asyncio.wait_for(read(), timeout=5)
        except Exception as error:
            snapshot = dict.fromkeys(previous)
            log.warning("Replica capacity snapshot unavailable: %s", type(error).__name__)
        now = time.monotonic()
        for cluster in previous.keys() - snapshot.keys():
            zero_since.pop(cluster, None)
            trusted.pop(cluster, None)
        current = {}
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
        try:
            await asyncio.wait_for(changed.wait(), timeout=interval)
        except TimeoutError:
            pass
        await asyncio.sleep(1)


class ReplicaPlugin(Plugin):
    def async_tasks(self):
        changed = asyncio.Event()
        return [observe_workers(changed), reconcile(changed)]
