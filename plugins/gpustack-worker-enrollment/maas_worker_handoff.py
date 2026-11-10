"""Single-use startup permits and credential fencing for enrolled workers."""

from datetime import datetime, timedelta, timezone
from hashlib import sha256
from hmac import compare_digest
import json
import re
from secrets import token_hex
from typing import Annotated
from uuid import UUID

from fastapi import Depends, HTTPException, Request
from fastapi.responses import JSONResponse, Response
from fastapi.security.utils import get_authorization_scheme_param
from gpustack import envs
from gpustack.api.auth import api_key_header_auth, authenticate_request, basic_auth, bearer_auth, cookie_auth
from gpustack.api.auth import get_user_from_api_token
from gpustack.api.exceptions import HTTPException as NativeHTTPException
from gpustack.schemas.api_keys import ApiKey
from gpustack.schemas.clusters import Cluster
from gpustack.schemas.principals import Principal, PrincipalType
from gpustack.schemas.workers import Worker, WorkerCreate, WorkerStateEnum, WorkerStatus, WorkerStatusStored
from gpustack.server.cache import delete_cache_by_key
from gpustack.server.db import async_session
from gpustack.server.services import APIKeyService, UserService, WorkerService
from pydantic import BaseModel, ConfigDict, PositiveInt, StringConstraints, TypeAdapter, ValidationError
from sqlalchemy import Column, DateTime, ForeignKey, Integer, MetaData, String, Table, delete, or_, text, update
from sqlmodel import select
from starlette._utils import get_route_path


PERMIT_PREFIX = "gpustack_maas-handoff_"
PERMIT_TTL = timedelta(minutes=5)
REGISTRATION = "/v2/workers"
STATUS = "/v2/worker-status"
HEARTBEAT = "/v2/worker-heartbeat"
MANAGEMENT = re.compile(r"/v2/(workers|clusters)/([^/]+)(?:/(registration-token|manifests))?")
RESOURCE_ID = TypeAdapter(int)

handoffs = Table(
    "maas_worker_handoffs", MetaData(),
    Column("worker_id", Integer, ForeignKey(Worker.__table__.c.id, ondelete="CASCADE"), primary_key=True),
    Column("session_id", String(36), nullable=False),
    Column("permit_hash", String(64), nullable=False),
    Column("expires_at", DateTime(timezone=True), nullable=False),
    Column("phase", String(16), nullable=False),
    Column("fence_receipt", String(128)),
)


class HandoffRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    worker_id: PositiveInt
    session_id: UUID
    gpu_uuid: str | None = None
    previous_session_id: UUID | None = None
    fence_receipt: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=128)] | None = None


def now():
    return datetime.now(timezone.utc)


def digest(value):
    return sha256(value.encode()).hexdigest()


def access_token(request):
    scheme, token = get_authorization_scheme_param(request.headers.get("Authorization"))
    return token if scheme.lower() == "bearer" else request.headers.get("X-API-Key", "")


async def handoff_for(session, worker_id):
    rows = await session.execute(select(handoffs).where(handoffs.c.worker_id == worker_id))
    return rows.mappings().one_or_none()


class WorkerHandoff:
    def __init__(self, app, cluster_id, require_maas):
        self.cluster_id = cluster_id
        self.initialized = False
        app.post("/internal/maas/worker-handoff", dependencies=[Depends(require_maas)],
                 include_in_schema=False)(self.issue_permit)
        app.middleware("http")(self.guard)

    async def initialize(self):
        async with async_session() as session:
            await session.execute(text("SELECT pg_advisory_xact_lock(hashtextextended('maas-worker-handoffs-schema', 0))"))
            await session.run_sync(lambda db: handoffs.create(db.connection(), checkfirst=True))
            await session.commit()
        self.initialized = True

    def managed(self, worker):
        return (worker is not None and worker.deleted_at is None and worker.cluster_id == self.cluster_id
                and bool(worker.external_id and worker.external_id.startswith("GPU-")))

    def require_initialized(self):
        if not self.initialized:
            raise HTTPException(503, "Worker handoff is initializing")

    async def issue_permit(self, request: HandoffRequest):
        self.require_initialized()
        permit = PERMIT_PREFIX + token_hex(32)
        session_id = str(request.session_id)
        expires_at = now() + PERMIT_TTL
        async with async_session() as session:
            worker = await Worker.one_by_id(session, request.worker_id, for_update=True)
            if (not self.managed(worker) or worker.name != worker.external_id
                    or (request.gpu_uuid is not None and request.gpu_uuid != worker.external_id)):
                raise HTTPException(404, "Enrolled worker not found")
            previous = await handoff_for(session, worker.id)
            keys = []
            principal = None
            principal_id = worker.system_principal_id
            values = {"session_id": session_id, "permit_hash": digest(permit), "expires_at": expires_at,
                      "phase": "reserved", "fence_receipt": request.fence_receipt}
            if previous is None:
                if principal_id is not None or request.previous_session_id is not None or request.fence_receipt:
                    raise HTTPException(409, "Existing identity requires an explicit migration")
                await session.execute(handoffs.insert().values(worker_id=worker.id, **values))
            else:
                if (previous["phase"] == "registering" or session_id == previous["session_id"]
                        or str(request.previous_session_id) != previous["session_id"] or not request.fence_receipt):
                    raise HTTPException(409, "Current session must be fenced before handoff")
                if principal_id is not None:
                    last_heartbeat = worker.heartbeat_time
                    stale_before = now() - timedelta(seconds=envs.WORKER_HEARTBEAT_GRACE_PERIOD)
                    if worker.state != WorkerStateEnum.NOT_READY or (last_heartbeat and last_heartbeat >= stale_before):
                        raise HTTPException(409, "Current worker must be offline before handoff")
                    principal = await Principal.one_by_id(session, principal_id)
                    if principal is None or principal.kind != PrincipalType.SYSTEM or await Cluster.one_by_field(
                        session, "system_principal_id", principal_id
                    ) is not None:
                        raise HTTPException(409, "Existing worker credential binding needs review")
                    keys = (await session.execute(delete(ApiKey).where(ApiKey.user_id == principal_id)
                                                  .returning(ApiKey.access_key))).scalars().all()
                await WorkerService(session).update(worker, {
                    "token": None, "system_principal_id": None,
                    "state": WorkerStateEnum.INITIALIZING, "heartbeat_time": None,
                    "state_message": "Waiting for diskless worker handoff", "status": WorkerStatus.get_default_status(),
                    "proxy_address": None, "advertise_address": None, "ip": "", "unreachable": False,
                }, auto_commit=False)
                if principal is not None:
                    await principal.delete(session, auto_commit=False)
                await session.execute(handoffs.update().where(handoffs.c.worker_id == worker.id).values(**values))
            await session.commit()
            for access_key in keys:
                await delete_cache_by_key(APIKeyService.get_by_access_key, access_key)
            if principal_id is not None:
                await delete_cache_by_key(UserService.get_by_id, principal_id)
            return {"cluster_id": worker.cluster_id, "worker_id": worker.id,
                    "worker_name": worker.name, "external_id": worker.external_id,
                    "session_id": session_id, "registration_token": permit,
                    "expires_at": expires_at.isoformat()}

    async def claim_registration(self, worker_in, token):
        async with async_session() as session:
            rows = (await session.exec(select(Worker).where(
                Worker.cluster_id == self.cluster_id, Worker.deleted_at.is_(None), Worker.external_id.like("GPU-%"),
                or_(Worker.external_id == worker_in.external_id, Worker.name == worker_in.name,
                    Worker.worker_uuid == worker_in.worker_uuid),
            ).with_for_update())).all()
            if not rows and not token.startswith(PERMIT_PREFIX):
                return None
            self.require_initialized()
            if len(rows) != 1 or not token.startswith(PERMIT_PREFIX):
                raise HTTPException(403, "A scoped startup permit is required")
            worker = rows[0]
            if (worker_in.cluster_id not in (None, self.cluster_id) or worker_in.name != worker.name
                    or worker_in.external_id != worker.external_id or worker.name != worker.external_id):
                raise HTTPException(403, "Startup permit does not match the worker binding")
            state = await handoff_for(session, worker.id)
            if (state is None or state["phase"] != "reserved" or state["expires_at"] <= now()
                    or not compare_digest(state["permit_hash"], digest(token))):
                raise HTTPException(403, "Startup permit is invalid, expired or used")
            cluster = await Cluster.one_by_id(session, self.cluster_id)
            if cluster is None or cluster.deleted_at is not None or not cluster.registration_token:
                raise HTTPException(503, "Enrollment cluster is unavailable")
            # Persist consumption before the native transaction can change the worker.
            await session.execute(handoffs.update().where(handoffs.c.worker_id == worker.id).values(phase="registering"))
            await session.commit()
            return worker.id, cluster.registration_token

    async def finish_registration(self, worker_id, success):
        async with async_session() as session:
            worker = await Worker.one_by_id(session, worker_id, for_update=True)
            await session.execute(handoffs.update().where(handoffs.c.worker_id == worker_id,
                                                         handoffs.c.phase == "registering")
                                  .values(phase="active" if success else "uncertain"))
            await session.commit()
            if worker is not None and worker.system_principal_id is not None:
                await delete_cache_by_key(UserService.get_by_id, worker.system_principal_id)

    async def guard_registration(self, request, call_next, token):
        try:
            worker_in = WorkerCreate.model_validate(await request.json())
        except (json.JSONDecodeError, UnicodeDecodeError, ValidationError):
            return await call_next(request)
        claimed = await self.claim_registration(worker_in, token)
        if claimed is None:
            return await call_next(request)
        worker_id, cluster_token = claimed
        request.scope["headers"] = [
            (name, value) for name, value in request.scope["headers"]
            if name not in {b"authorization", b"x-api-key", b"cookie"}
        ] + [(b"authorization", f"Bearer {cluster_token}".encode())]
        success = False
        try:
            response = await call_next(request)
            success = response.status_code == 200
            return response
        finally:
            await self.finish_registration(worker_id, success)

    async def report(self, request, token, path):
        async with async_session() as session:
            user, _ = await get_user_from_api_token(session, token)
            if user is None or not self.managed(user.worker):
                return None
            self.require_initialized()
            report = None
            if path == STATUS:
                try:
                    report = WorkerStatusStored.model_validate(await request.json())
                except (json.JSONDecodeError, UnicodeDecodeError, ValidationError):
                    raise HTTPException(422, "Invalid worker status") from None
            worker = await Worker.one_by_id(session, user.worker.id, for_update=True)
            if (not user.is_active or not self.managed(worker) or not worker.token
                    or not compare_digest(worker.token.encode(), token.encode())):
                raise HTTPException(401, "Worker credential has been fenced")
            state = await handoff_for(session, worker.id)
            if state is None or state["phase"] != "active":
                raise HTTPException(403, "Worker session is not active")

            heartbeat_time = now().replace(microsecond=0)
            if report is not None:
                if report.worker_uuid != worker.worker_uuid:
                    raise HTTPException(409, "Worker UUID does not match the active session")
                for key in report.model_fields_set:
                    setattr(worker, key, getattr(report, key))
                worker.heartbeat_time = heartbeat_time
                worker.compute_state()
                # Apply managed reports under the same row lock as rotation. A
                # queued report keyed only by worker_id could outlive its token.
                await WorkerService(session).update(worker, auto_commit=False)
            else:
                await session.execute(update(Worker).where(Worker.id == worker.id)
                                      .values(heartbeat_time=heartbeat_time))
            await session.commit()
            return Response(status_code=204)

    async def protect_management(self, request, path):
        match = MANAGEMENT.fullmatch(path)
        if match is None:
            return
        resource, raw_id, secret = match.groups()
        try:
            resource_id = RESOURCE_ID.validate_python(raw_id)
        except ValidationError:
            return
        reads_secret = resource == "clusters" and secret is not None and request.method == "GET"
        mutation = secret is None and request.method in {"PUT", "DELETE"}
        if not reads_secret and not mutation:
            return

        protected = resource_id == self.cluster_id
        if resource == "workers":
            async with async_session() as session:
                protected = self.managed(await Worker.one_by_id(session, resource_id))
        if not protected and not reads_secret:
            return
        user = await authenticate_request(
            request, basic_credentials=await basic_auth(request), bearer_token=await bearer_auth(request),
            x_api_key=await api_key_header_auth(request), cookie_token=await cookie_auth(request),
        )
        if user.kind == PrincipalType.SYSTEM and (protected or self.managed(user.worker)):
            raise HTTPException(403, "System credentials cannot manage protected worker bindings or read cluster secrets")

    async def guard(self, request: Request, call_next):
        path = get_route_path(request.scope).rstrip("/")
        try:
            if request.method != "POST" or path not in {REGISTRATION, STATUS, HEARTBEAT}:
                await self.protect_management(request, path)
                return await call_next(request)
            token = access_token(request)
            if path == REGISTRATION:
                return await self.guard_registration(request, call_next, token)
            if token:
                response = await self.report(request, token, path)
                if response is not None:
                    return response
            return await call_next(request)
        except HTTPException as error:
            return JSONResponse({"detail": error.detail}, status_code=error.status_code, headers=error.headers)
        except NativeHTTPException as error:
            return JSONResponse({"code": error.status_code, "reason": error.reason, "message": error.message},
                                status_code=error.status_code)
