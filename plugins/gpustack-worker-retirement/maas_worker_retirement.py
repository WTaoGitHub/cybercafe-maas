"""GPUStack v2.2.3 server plugin for retirement of offline E5 workers."""
import asyncio
from datetime import datetime, timezone
from importlib.metadata import version
import logging
import os
import time

from gpustack.extension import Plugin
from gpustack.schemas.models import Model, ModelInstance
from gpustack.schemas.workers import Worker
from gpustack.server.db import async_session
from gpustack.server.services import WorkerService
from gpustack.server.worker_status_buffer import (
    heartbeat_flush_buffer,
    heartbeat_flush_buffer_lock,
    worker_status_flush_buffer,
    worker_status_flush_buffer_lock,
)
from gpustack.utils.model_instance_workers import get_model_instance_worker_match
from sqlalchemy import text
from sqlmodel import select

from maas_retirement_policy import INTERVAL, RetirementPolicy, ready_cards

log = logging.getLogger(__name__)


async def workers_in_cluster(session, cluster_id):
    result = await session.exec(
        select(Worker).where(Worker.cluster_id == cluster_id, Worker.deleted_at.is_(None))
    )
    return result.all()


async def has_references(session, worker):
    instances = await ModelInstance.all_by_field(session, "cluster_id", worker.cluster_id)
    if any(
        instance.deleted_at is None and get_model_instance_worker_match(
            instance, worker_name=worker.name, worker_id=worker.id
        ).matched for instance in instances
    ):
        return True
    models = await Model.all_by_field(session, "cluster_id", worker.cluster_id)
    return any(
        gpu_id.partition(":")[0] == worker.name
        for model in models if model.deleted_at is None and model.gpu_selector
        for gpu_id in model.gpu_selector.gpu_ids or ()
    )


async def recheck(cluster_id, candidate_id, policy, apply):
    # Check queued updates while preventing new entries in this server process.
    async with heartbeat_flush_buffer_lock, worker_status_flush_buffer_lock:
        async with async_session() as session:
            if apply and session.get_bind().dialect.name == "sqlite":
                await session.execute(text("BEGIN IMMEDIATE"))
            result = await session.exec(
                select(Worker).where(
                    Worker.cluster_id == cluster_id, Worker.id == candidate_id,
                    Worker.deleted_at.is_(None),
                ).with_for_update()
            )
            candidate = result.one_or_none()
            if candidate is None:
                return False
            if candidate_id in heartbeat_flush_buffer or candidate_id in worker_status_flush_buffer:
                return False
            workers = await workers_in_cluster(session, cluster_id)
            cards = ready_cards(workers)
            reason = policy.reason(candidate, cards, datetime.now(timezone.utc), time.monotonic())
            if not reason or await has_references(session, candidate):
                return False
            if apply:
                await WorkerService(session).delete(candidate, soft=False)
            log.info("Worker retirement %s cluster_id=%s worker_id=%s reason=%s",
                     "applied" if apply else "preview", cluster_id, candidate_id, reason)
            return True


async def reconcile(cluster_id, protected_ids, apply):
    policy = RetirementPolicy(protected_ids)
    while True:
        try:
            async with async_session() as session:
                workers = await workers_in_cluster(session, cluster_id)
            now, tick = datetime.now(timezone.utc), time.monotonic()
            policy.observe(workers, now, tick)
            cards = ready_cards(workers)
            for worker in sorted(workers, key=lambda row: row.id):
                if policy.reason(worker, cards, now, tick):
                    if await recheck(cluster_id, worker.id, policy, apply) and apply:
                        break
        except Exception:
            policy.ready_since.clear()
            log.exception("Worker retirement scan failed for cluster_id=%s", cluster_id)
        await asyncio.sleep(INTERVAL)


class WorkerRetirementPlugin(Plugin):
    def async_tasks(self):
        mode = os.getenv("MAAS_WORKER_RETIREMENT_MODE", "off")
        if mode == "off":
            return []
        try:
            cluster_id = int(os.environ["MAAS_WORKER_RETIREMENT_CLUSTER_ID"])
            protected_ids = frozenset(
                int(value) for value in os.getenv("MAAS_WORKER_RETIREMENT_PROTECTED_IDS", "").split(",") if value
            )
            if (mode not in {"preview", "apply"} or cluster_id <= 0
                    or any(value <= 0 for value in protected_ids) or version("gpustack") != "2.2.3"):
                raise ValueError("Invalid retirement mode, scope or GPUStack version")
        except (KeyError, ValueError) as error:
            log.error("Worker retirement disabled: %s", error)
            return []
        return [reconcile(cluster_id, protected_ids, mode == "apply")]
