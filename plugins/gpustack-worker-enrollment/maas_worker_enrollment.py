"""Pre-enroll a diskless GPUStack v2.2.3 Docker worker through MaaS."""

from hmac import compare_digest
import logging
import os
from pathlib import Path
import re
from uuid import uuid4

from fastapi import Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from gpustack import __version__
from gpustack.extension import Plugin
from gpustack.schemas.clusters import Cluster, ClusterProvider
from gpustack.schemas.workers import Worker, WorkerStateEnum, WorkerStatus
from gpustack.server.db import async_session
from pydantic import BaseModel, ConfigDict, PositiveInt, field_validator
from sqlalchemy import and_, func, or_, text
from sqlalchemy.exc import IntegrityError
from sqlmodel import select

from maas_worker_handoff import WorkerHandoff


log = logging.getLogger(__name__)
GPU_UUID = re.compile(r"GPU-[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}", re.IGNORECASE)


class EnrollmentRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    cluster_id: PositiveInt
    gpu_uuid: str

    @field_validator("gpu_uuid")
    @classmethod
    def canonical_gpu_uuid(cls, value: str) -> str:
        if not GPU_UUID.fullmatch(value):
            raise ValueError("Expected a complete NVIDIA GPU UUID")
        return "GPU-" + value[4:].lower()


class Enrollment(BaseModel):
    cluster_id: int
    worker_id: int
    worker_name: str
    external_id: str


async def enroll(request: EnrollmentRequest) -> Enrollment:
    gpu_uuid = request.gpu_uuid
    async with async_session() as session:
        if session.get_bind().dialect.name != "postgresql":
            raise HTTPException(503, "Worker enrollment requires PostgreSQL")

        # GPUStack worker names are server-wide. Use the same lock across clusters
        # and server processes, even when the worker row does not yet exist.
        await session.execute(
            text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"),
            {"key": f"maas-worker-enrollment:{gpu_uuid}"},
        )
        cluster = await Cluster.one_by_id(session, request.cluster_id)
        if cluster is None or cluster.deleted_at is not None or cluster.provider != ClusterProvider.Docker:
            raise HTTPException(404, "Enrollment cluster is not an active Docker cluster")

        rows = (await session.exec(select(Worker).where(or_(
            func.lower(Worker.name) == gpu_uuid.lower(),
            and_(
                Worker.cluster_id == cluster.id,
                Worker.deleted_at.is_(None),
                func.lower(Worker.external_id) == gpu_uuid.lower(),
            ),
        )).with_for_update())).all()
        if rows:
            worker = rows[0]
            if (len(rows) != 1 or worker.cluster_id != cluster.id or worker.deleted_at is not None
                    or worker.name != gpu_uuid or worker.external_id != gpu_uuid
                    or worker.state == WorkerStateEnum.DELETING):
                raise HTTPException(409, "GPU UUID conflicts with an existing worker record")
        else:
            try:
                worker = await Worker.create(session, Worker(
                    name=gpu_uuid,
                    external_id=gpu_uuid,
                    cluster_id=cluster.id,
                    owner_principal_id=cluster.owner_principal_id,
                    provider=cluster.provider,
                    hostname="",
                    ip="",
                    ifname="",
                    port=0,
                    # A fresh native worker sends an empty UUID. Give the row
                    # a distinct value so an unrelated registration cannot match it.
                    worker_uuid=str(uuid4()),
                    state=WorkerStateEnum.INITIALIZING,
                    state_message="Waiting for native worker registration",
                    status=WorkerStatus.get_default_status(),
                    labels={"worker-name": gpu_uuid},
                ), auto_commit=False)
                await session.commit()
            except IntegrityError:
                raise HTTPException(409, "GPU UUID conflicts with an existing worker record") from None

        return Enrollment(cluster_id=worker.cluster_id, worker_id=worker.id,
                          worker_name=worker.name, external_id=worker.external_id)


class WorkerEnrollmentPlugin(Plugin):
    def __init__(self, app, cfg):
        self.handoff = None
        cluster_scope = os.getenv("MAAS_WORKER_ENROLLMENT_CLUSTER_ID")
        key_file = os.getenv("MAAS_WORKER_ENROLLMENT_KEY_FILE")
        handoff_mode = os.getenv("MAAS_WORKER_HANDOFF_MODE", "off")
        if handoff_mode not in {"off", "required"}:
            raise ValueError("Invalid MAAS_WORKER_HANDOFF_MODE")
        if cluster_scope is None and key_file is None and handoff_mode == "off":
            return
        try:
            if cluster_scope is None or key_file is None or __version__.removeprefix("v") != "2.2.3":
                raise ValueError("Invalid enrollment configuration")
            cluster_id = int(cluster_scope)
            key = Path(key_file).read_bytes().strip()
            if cluster_id <= 0 or len(key) < 32 or not re.fullmatch(rb"[!-~]+", key):
                raise ValueError("Invalid enrollment configuration")
        except (OSError, ValueError):
            if handoff_mode == "required":
                raise ValueError("Worker handoff requires valid enrollment configuration") from None
            log.error("Worker enrollment disabled due to invalid configuration")
            return

        bearer = HTTPBearer(auto_error=False)

        async def require_maas(credentials: HTTPAuthorizationCredentials | None = Depends(bearer)):
            if credentials is None or not compare_digest(credentials.credentials.encode(), key):
                raise HTTPException(401, "MaaS enrollment credential required",
                                    headers={"WWW-Authenticate": "Bearer"})

        @app.post("/internal/maas/worker-enrollment", dependencies=[Depends(require_maas)], include_in_schema=False)
        async def pre_enroll(request: EnrollmentRequest) -> Enrollment:
            if request.cluster_id != cluster_id:
                raise HTTPException(403, "Cluster is outside the enrollment scope")
            return await enroll(request)

        if handoff_mode == "required":
            self.handoff = WorkerHandoff(app, cluster_id, require_maas)

    def async_tasks(self):
        return [self.handoff.initialize()] if self.handoff else []
