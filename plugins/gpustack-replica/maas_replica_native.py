"""GPUStack v2.2.3 management adapter for the isolated execution trial."""
from collections import defaultdict
import hashlib
import json

from gpustack.schemas.models import ModelUpdate

from maas_replica_execution import Deployment, Snapshot


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def model_settings(row):
    # Omit read-only server fields while keeping the native model's settings.
    return ModelUpdate.model_validate(row).model_dump(mode="json")


def binding(row):
    settings = model_settings(row)
    settings.pop("replicas")
    settings.pop("ready_replicas")
    return digest(settings)


class NativeAdapter:
    def __init__(self, request, *, revision, cluster_id, owner_id, model_ids):
        self.request = request
        self.revision = revision
        self.cluster_id = cluster_id
        self.owner_id = owner_id
        self.model_ids = frozenset(model_ids)

    async def _pages(self, path):
        items = []
        ids = set()
        for page in range(1, 11):
            result = await self.request("GET", f"{path}?perPage=100&page={page}")
            batch = result["items"]
            for row in batch:
                if row["id"] in ids:
                    raise ValueError("Duplicate object in management API inventory")
                ids.add(row["id"])
                items.append(row)
            if len(batch) < 100:
                return items
        raise ValueError("Management API inventory exceeded the supported bound")

    async def read(self):
        workers = await self._pages("/v2/workers")
        models = await self._pages("/v2/models")
        instances = await self._pages("/v2/model-instances")
        cards = {}
        for worker in workers:
            if (worker["state"] != "ready" or worker.get("unreachable")
                    or (worker.get("maintenance") or {}).get("enabled")):
                continue
            inventory = (worker.get("status") or {}).get("gpu_devices")
            if inventory is None:
                raise ValueError("Ready worker has no device inventory")
            for gpu in inventory:
                key = (worker["id"], gpu["index"])
                if key in cards or not gpu.get("uuid"):
                    raise ValueError("Incomplete GPU identity")
                memory = gpu.get("memory") or {}
                reserved = (worker.get("system_reserved") or {}).get("vram")
                capacity = (memory.get("total"), memory.get("allocated"), reserved)
                if any(type(value) is not int or value < 0 for value in capacity):
                    raise ValueError("Incomplete GPU allocation")
                cards[key] = (gpu["uuid"], *capacity)
        if len({card[0] for card in cards.values()}) != len(cards):
            raise ValueError("Duplicate eligible GPU identity")
        model_ids = {model["id"] for model in models}
        rows = defaultdict(list)
        for instance in instances:
            if instance["model_id"] not in model_ids:
                raise ValueError("Instance inventory drifted during read")
            rows[instance["model_id"]].append(instance)
        deployments = {}
        for model in models:
            healthy = tuple(
                row["state"] == "running" and bool(row.get("gpu_indexes")) and
                all((row["worker_id"], index) in cards for index in row["gpu_indexes"])
                for row in sorted(rows[model["id"]], key=lambda row: row["id"])
            )
            deployments[model["id"]] = Deployment(model["cluster_id"], model["owner_principal_id"],
                                                   binding(model), model["replicas"], healthy)
        identity = [(i, m.cluster_id, m.owner_id, m.binding, m.replicas, m.healthy)
                    for i, m in sorted(deployments.items())]
        placement = sorted((row["id"], row["model_id"], row["state"], row["worker_id"],
                            row.get("gpu_indexes")) for row in instances)
        fingerprint = digest([sorted(cards.items()), identity, placement])
        return Snapshot(self.revision, fingerprint, deployments)

    async def write(self, action):
        if (action.model_id not in self.model_ids or action.cluster_id != self.cluster_id
                or action.owner_id != self.owner_id or action.revision != self.revision):
            raise ValueError("Replica write outside the isolated deployment binding")
        path = f"/v2/models/{action.model_id}"
        row = await self.request("GET", path)
        if (row["id"], row["cluster_id"], row["owner_principal_id"], row["replicas"], binding(row)) != (
            action.model_id, action.cluster_id, action.owner_id, action.before, action.binding
        ):
            raise ValueError("Native deployment changed before the write")
        await self.request("PUT", path, {**model_settings(row), "replicas": action.after})
