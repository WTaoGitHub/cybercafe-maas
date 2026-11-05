"""Replica execution guard for an injected transport.

The observe-mode Server plugin does not import or activate this module.
"""
import asyncio
from dataclasses import asdict, dataclass, field
import json
import os
from pathlib import Path
import stat
from tempfile import NamedTemporaryFile


@dataclass(frozen=True)
class Deployment:
    cluster_id: int
    owner_id: int
    binding: str  # Fingerprint of native model settings excluding replica counts.
    replicas: int
    healthy: tuple[bool, ...]  # One entry per observed, non-deleted instance.

    def __post_init__(self):
        if type(self.replicas) is not int or self.replicas < 0:
            raise ValueError("Invalid native replica count")
        if any(type(value) is not bool for value in self.healthy):
            raise ValueError("Incomplete instance health snapshot")


@dataclass(frozen=True)
class Snapshot:
    revision: int
    fingerprint: str  # Capacity and state relevant to the planned action.
    deployments: dict[int, Deployment]
    complete: bool = True
    gpu_inventory: dict = field(default_factory=dict)


@dataclass(frozen=True)
class Action:
    revision: int
    fingerprint: str
    model_id: int
    cluster_id: int
    owner_id: int
    binding: str
    before: int
    after: int


def inflight(deployments, cluster_id, pending=None):
    """Conservatively count desired slots and visible instances once each."""
    global_count = cluster_count = 0
    for key, model in deployments.items():
        target = model.replicas
        if pending is not None and key == pending.model_id:
            target = max(target, pending.after)
        count = max(target, len(model.healthy)) - sum(model.healthy)
        global_count += count
        if model.cluster_id == cluster_id:
            cluster_count += count
    if pending is not None and pending.model_id not in deployments:
        raise ValueError("Pending write cannot be matched to a deployment")
    return global_count, cluster_count


def validate_action(snapshot, action, *, floor, batch_max, global_max, cluster_max):
    if any(type(value) is not int or value < 0
           for value in (floor, batch_max, global_max, cluster_max, action.after)):
        raise ValueError("Invalid execution bounds")
    if not snapshot.complete or (snapshot.revision, snapshot.fingerprint) != (
        action.revision, action.fingerprint
    ):
        raise ValueError("Stale or incomplete execution snapshot")
    model = snapshot.deployments.get(action.model_id)
    if model is None or (model.cluster_id, model.owner_id, model.binding, model.replicas) != (
        action.cluster_id, action.owner_id, action.binding, action.before
    ):
        raise ValueError("Deployment no longer matches the action")
    delta = action.after - action.before
    if not delta or abs(delta) > batch_max:
        raise ValueError("Action exceeds the batch bounds")
    if delta > 0:
        total, local = inflight(snapshot.deployments, action.cluster_id)
        # Visible surplus instances may consume pressure even if the new
        # target does not increase the number of outstanding slots.
        added = max(action.after, len(model.healthy)) - max(action.before, len(model.healthy))
        if total + added > global_max or local + added > cluster_max:
            raise ValueError("In-flight startup budget exhausted")
        return
    if action.after < floor:
        raise ValueError("Target violates the automatic scale-down floor")
    healthy = sum(model.healthy)
    worst_case_removals = max(model.replicas, len(model.healthy)) - action.after
    if healthy and healthy - worst_case_removals < min(healthy, floor):
        raise ValueError("Native scale-down could remove a protected healthy instance")


class PendingWrite:
    """Private, durable record of one unresolved request for the single writer."""

    def __init__(self, path):
        self.path = Path(path)

    def _check_parent(self):
        if self.path.parent.stat().st_mode & 0o077:
            raise ValueError("Write journal directory must be private")

    def read(self):
        self._check_parent()
        try:
            fd = os.open(self.path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        except FileNotFoundError:
            return None
        with os.fdopen(fd, "rb") as handle:
            info = os.fstat(handle.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_mode & 0o077:
                raise ValueError("Write journal must be a private regular file")
            return Action(**json.loads(handle.read(65537)))

    def _sync_parent(self):
        fd = os.open(self.path.parent, os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)

    def reserve(self, action):
        self._check_parent()
        with NamedTemporaryFile(mode="w", dir=self.path.parent) as handle:
            json.dump(asdict(action), handle)
            handle.flush()
            os.fsync(handle.fileno())
            os.link(handle.name, self.path)  # Fail if an unresolved request already exists.
            self._sync_parent()

    def clear(self):
        self.path.unlink()
        self._sync_parent()


class WriteNotSent(Exception):
    """The final local gate rejected the request before transport dispatch."""


class Executor:
    """Run a bounded write through an injected adapter and confirm by readback."""

    def __init__(self, journal, read, write, *, write_timeout=2, read_timeout=5):
        self.journal, self.read, self.write = journal, read, write
        self.write_timeout = write_timeout
        self.read_timeout = read_timeout
        self.lock = asyncio.Lock()

    async def _recover(self):
        pending = self.journal.read()
        if pending is None:
            return "idle"
        try:
            snapshot = await asyncio.wait_for(self.read(), timeout=self.read_timeout)
        except (OSError, TimeoutError):
            return "unresolved"
        if not snapshot.complete:
            return "unresolved"
        model = snapshot.deployments.get(pending.model_id)
        if model is None or (model.cluster_id, model.owner_id, model.binding) != (
            pending.cluster_id, pending.owner_id, pending.binding
        ):
            return "conflict"
        if model.replicas == pending.after:
            if pending.after < pending.before and len(model.healthy) > pending.after:
                return "converging"
            self.journal.clear()
            return "confirmed"
        # The old value is not proof that a timed-out request did not commit.
        return "unresolved" if model.replicas == pending.before else "conflict"

    async def recover(self):
        async with self.lock:
            return await self._recover()

    async def submit(self, action, *, floor, batch_max, global_max, cluster_max):
        async with self.lock:
            if self.journal.read() is not None:
                return "unresolved"
            snapshot = await asyncio.wait_for(self.read(), timeout=self.read_timeout)
            validate_action(snapshot, action, floor=floor, batch_max=batch_max,
                            global_max=global_max, cluster_max=cluster_max)
            self.journal.reserve(action)
            try:
                await asyncio.wait_for(self.write(action), timeout=self.write_timeout)
            except WriteNotSent as error:
                self.journal.clear()
                return str(error)
            except (OSError, TimeoutError):
                return "unresolved"
            return await self._recover()
