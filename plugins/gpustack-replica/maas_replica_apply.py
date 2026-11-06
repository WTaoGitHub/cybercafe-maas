"""Opt-in single-writer bridge from arrival previews to native scale-up."""
import asyncio
import json
import os
from pathlib import Path
import stat
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener

from maas_replica_execution import Action, Executor, PendingWrite, WriteNotSent
from maas_replica_native import NativeAdapter
from maas_replica_policy import ARRIVAL_EVENTS
from maas_replica_pressure import PressureGate


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class LocalManagement:
    def __init__(self, url, key_path):
        parsed = urlsplit(url)
        if (parsed.scheme != "http" or parsed.hostname != "127.0.0.1" or not parsed.port
                or parsed.username or parsed.password or parsed.path not in ("", "/")
                or parsed.query or parsed.fragment):
            raise ValueError("Apply requires a loopback management endpoint")
        self.url = url.rstrip("/")
        self.key_path = key_path
        self.opener = build_opener(ProxyHandler({}), NoRedirect())

    def _headers(self):
        fd = os.open(self.key_path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(fd) as handle:
            mode = os.fstat(handle.fileno()).st_mode
            if not stat.S_ISREG(mode) or mode & 0o077:
                raise ValueError("Management key must be a private regular file")
            key = handle.read(8193).strip()
        if not key or len(key) > 8192 or any(ch.isspace() for ch in key):
            raise ValueError("Invalid management key")
        return {"Authorization": "Bearer " + key, "Content-Type": "application/json"}

    def _request(self, method, path, body):
        data = json.dumps(body).encode() if body is not None else None
        req = Request(self.url + path, data, self._headers(), method=method)
        with self.opener.open(req, timeout=4) as response:
            return json.load(response)

    async def __call__(self, method, path, body=None):
        return await asyncio.to_thread(self._request, method, path, body)


class ScaleUp:
    def __init__(self, cluster, journal, request, *, pressure=None):
        self.cluster = cluster
        self.journal = journal
        self.request = request
        self.version_checked = False
        self.pressure = pressure if pressure is not None else PressureGate(None)

    @classmethod
    def from_environment(cls):
        cluster = os.environ.get("MAAS_REPLICA_APPLY_CLUSTER")
        if cluster is None:
            return None
        ident = int(cluster)
        if ident < 1:
            raise ValueError("Invalid approved cluster")
        path = Path(os.environ["MAAS_REPLICA_WRITE_JOURNAL"])
        if not path.is_absolute():
            raise ValueError("Write journal must have an absolute path")
        journal = PendingWrite(path)
        journal.read()
        request = LocalManagement(os.environ["MAAS_REPLICA_MANAGEMENT_URL"],
                                  os.environ["MAAS_REPLICA_MANAGEMENT_KEY_FILE"])
        return cls(ident, journal, request, pressure=PressureGate.from_environment())

    async def _adapter(self, policy, request):
        if not self.version_checked:
            version = await self.request("GET", "/version")
            if version.get("version") != "v2.2.3":
                raise ValueError("Unapproved GPUStack version")
            self.version_checked = True
        return NativeAdapter(request, revision=policy.revision, cluster_id=self.cluster,
                             owner_id=policy.owner, model_ids=policy.models, grow_only=True)

    async def advance(self, policy, event, devices, plan, *, refresh, bindings):
        if policy.cluster != self.cluster:
            return "cluster_not_authorized"
        pending = self.journal.read()
        if pending is None and (policy.execution.get("mode") != "apply"
                                or event not in ARRIVAL_EVENTS
                                or not plan.get("up_preview_limit")):
            return "idle"
        if pending is None:
            pressure = self.pressure.evaluate(policy.pressure)
            if pressure != "open":
                return pressure

        async def checked_request(method, path, body=None):
            if method == "PUT":
                try:
                    unchanged = refresh() is policy
                except (OSError, ValueError, TypeError, KeyError, AttributeError):
                    unchanged = False
                if not unchanged:
                    raise WriteNotSent("policy_changed")
                pressure = self.pressure.evaluate(policy.pressure)
                if pressure != "open":
                    raise WriteNotSent(pressure)
            return await self.request(method, path, body)

        api = await self._adapter(policy, checked_request)
        executor = Executor(self.journal, api.read, api.write, write_timeout=12, read_timeout=12)
        if pending is not None:
            return await executor.recover()
        snapshot = await asyncio.wait_for(api.read(), timeout=12)
        expected = {(card.worker_id, card.index): (uuid, card.total_vram, card.allocated_vram,
                    card.reserved_vram, card.labels, card.kind) for uuid, card in devices.items()}
        if snapshot.gpu_inventory.get(self.cluster) != expected:
            return "capacity_changed"
        rules, execution = policy.effective(event)
        current = plan["current"]
        for ident, count in current.items():
            model = snapshot.deployments.get(ident)
            if (model is None or model.cluster_id != self.cluster
                    or model.owner_id != policy.owner or model.replicas != count
                    or model.binding != bindings.get(ident)
                    or sum(model.healthy) < count):
                return "deployment_changed_or_waiting"
        candidates = [ident for ident, count in current.items() if plan["policy_target"][ident] > count]
        if not candidates:
            return "idle"
        first = [ident for ident in candidates if current[ident] < rules[ident].min_replicas]
        ident = (min(first, key=lambda key: (rules[key].priority, key)) if first
                 else min(candidates, key=lambda key: (current[key] - plan["policy_target"][key], key)))
        if refresh() is not policy:
            return "policy_changed"
        model = snapshot.deployments[ident]
        action = Action(snapshot.revision, snapshot.fingerprint, ident, model.cluster_id,
                        model.owner_id, model.binding, model.replicas, model.replicas + 1)
        return await executor.submit(action, floor=0, batch_max=1,
                                     global_max=execution["max_inflight_global"],
                                     cluster_max=execution["max_inflight_per_cluster"])
