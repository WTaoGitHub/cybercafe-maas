"""Read-only worker capacity reconciliation for GPUStack v2.2.3."""
import asyncio
import logging
import time

from gpustack.extension import Plugin
from gpustack.schemas.workers import Worker
from gpustack.server.db import async_session

log = logging.getLogger(__name__)


def count_devices(workers):
    devices = set()
    for worker in workers:
        if worker.state != "ready" or worker.unreachable:
            continue
        if worker.maintenance and worker.maintenance.enabled:
            continue
        if worker.status is None or worker.status.gpu_devices is None:
            raise ValueError("Ready worker has no GPU inventory")
        for device in worker.status.gpu_devices:
            uuid = device.uuid
            if not uuid or uuid in devices:
                raise ValueError("Missing or duplicate GPU UUID")
            devices.add(uuid)
    return len(devices)


async def read_capacity():
    async with async_session() as session:
        return count_devices(await Worker.all_by_fields(session, {"deleted_at": None}))


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
    zero_since = None
    previous = None
    while True:
        changed.clear()
        try:
            count = await asyncio.wait_for(read(), timeout=5)
        except Exception as error:
            count = None
            zero_since = None
            state = "unknown"
            log.warning("Replica capacity snapshot unavailable: %s", type(error).__name__)
        else:
            now = time.monotonic()
            if count:
                state = "available"
                zero_since = None
            else:
                if zero_since is None:
                    zero_since = now
                state = "zero" if now - zero_since >= zero_grace else "zero_pending"
        current = (state, count)
        if current != previous:
            log.info("Replica capacity state=%s devices=%s", *current)
            previous = current
        try:
            await asyncio.wait_for(changed.wait(), timeout=interval)
        except TimeoutError:
            pass
        # A bounded delay coalesces event bursts without postponing periodic reads.
        await asyncio.sleep(1)


class ReplicaPlugin(Plugin):
    def async_tasks(self):
        changed = asyncio.Event()
        return [observe_workers(changed), reconcile(changed)]
