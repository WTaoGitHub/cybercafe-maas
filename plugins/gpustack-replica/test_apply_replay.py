import asyncio
from copy import deepcopy
from importlib.util import find_spec
import json
import logging
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import patch

if find_spec("gpustack") is None:
    raise unittest.SkipTest("Replay needs the pinned GPUStack v2.2.3 runtime")

from maas_replica import Device, plan_snapshot, reconcile
from maas_replica_apply import ScaleUp
from maas_replica_execution import PendingWrite
from maas_replica_native import binding
from maas_replica_policy import Policy, arrival_context
from maas_replica_pressure import PressureGate


def deployment(ident, cluster, replicas, ready):
    return dict(id=ident, cluster_id=cluster, owner_principal_id=1, name=f"test-model-{ident}",
                source="local_path", local_path="/test/model", backend="vllm",
                replicas=replicas, ready_replicas=ready, cpu_offloading=False,
                distributed_inference_across_workers=False, gpu_selector=None,
                worker_selector={}, env={"GPUSTACK_MODEL_VRAM_CLAIM": "3"})


def worker(ident, allocated):
    return dict(id=ident, cluster_id=1, state="ready", unreachable=False, maintenance=None,
                labels={}, system_reserved={"vram": 0},
                status={"gpu_devices": [dict(uuid=f"gpu-{ident}", index=0, type="cuda",
                                             memory={"total": 6, "allocated": allocated})]})


class Replay(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        tmp = TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.now = 100
        self.journal = PendingWrite(self.root / "pending.json")
        self.gate = PressureGate(self.root / "pressure.json", clock=lambda: self.now)
        self.policy = Policy({
            "schema_version": 1, "revision": 1,
            "scope": {"cluster_id": 1, "owner_principal_id": 1},
            "models": {"1": {"vram_claim_bytes": 3, "max_replicas": 3},
                       "2": {"vram_claim_bytes": 3, "max_replicas": 3, "priority": 2}},
            "execution": {"mode": "apply", "max_inflight_global": 1,
                          "max_inflight_per_cluster": 1, "scale_up_batch_max": 2},
            "pressure": {"max_sample_age_s": 10, "recovery_hold_s": 4,
                         "signals": {"registry_rejected": {"low": 0, "high": 1}}},
            "event_overrides": {"gpu_added_burst": {"execution": {"scale_up_batch_max": 1}}},
        }, allow_apply=True)
        self.models = {1: deployment(1, 1, 1, 1), 2: deployment(2, 1, 0, 0),
                       99: deployment(99, 2, 1, 0)}
        self.instances = [
            dict(id=1, model_id=1, worker_id=1, gpu_indexes=[0], state="running"),
            dict(id=99, model_id=99, worker_id=None, gpu_indexes=None, state="pending"),
        ]
        self.workers = [worker(i, 3 if i == 1 else 0) for i in range(1, 5)]
        self.writes = []
        self.before_put = None
        self.executor = ScaleUp(1, self.journal, self.request, pressure=self.gate)

    def sample(self, rejected):
        path = self.root / "pressure.json"
        path.write_text(json.dumps({"observed_at": self.now,
                                    "signals": {"registry_rejected": rejected}}))
        path.chmod(0o600)

    def cards(self):
        return {gpu["uuid"]: Device(w["id"], gpu["index"],
                                    gpu["memory"]["total"] - gpu["memory"]["allocated"],
                                    gpu["memory"]["total"], gpu["memory"]["allocated"], 0, (), "cuda")
                for w in self.workers for gpu in w["status"]["gpu_devices"]}

    async def request(self, method, path, body=None):
        path = path.split("?")[0]
        if path == "/version":
            return {"version": "v2.2.3"}
        inventory = {"/v2/workers": self.workers, "/v2/models": list(self.models.values()),
                     "/v2/model-instances": self.instances}
        if path in inventory:
            return {"items": deepcopy(inventory[path])}
        ident = int(path.rsplit("/", 1)[1])
        if method == "GET":
            if self.before_put:
                self.before_put()
            return deepcopy(self.models[ident])
        self.writes.append((ident, body["replicas"]))
        self.models[ident]["replicas"] = body["replicas"]
        self.instances.append(dict(id=100+len(self.writes), model_id=ident, worker_id=None,
                                   gpu_indexes=None, state="pending"))

    async def advance(self):
        cards = self.cards()
        event = arrival_context({"gpu-1"}, set(cards), self.policy)
        self.assertEqual(event, "gpu_added_burst")
        plan = plan_snapshot(self.policy, event, cards,
                             [SimpleNamespace(**m) for m in self.models.values()],
                             [SimpleNamespace(**i) for i in self.instances])
        return await self.executor.advance(self.policy, event, cards, plan,
                                           refresh=lambda: self.policy,
                                           bindings={i: binding(m) for i, m in self.models.items()})

    async def test_burst_waits_for_pending_and_pressure_then_admits_one_floor(self):
        self.sample(0)
        self.assertEqual(await self.advance(), "idle")
        self.assertEqual(self.writes, [])
        self.models[99]["replicas"] = 0
        self.instances.pop()
        self.sample(1)
        self.assertEqual(await self.advance(), "pressure_high")
        self.now = 111
        self.assertEqual(await self.advance(), "pressure_unavailable")
        self.now = 112
        self.sample(0)
        self.assertEqual(await self.advance(), "pressure_recovering")
        self.now = 116
        self.assertEqual(await self.advance(), "pressure_recovering")
        self.sample(0)
        # Pressure rises after the last native GET but before PUT dispatch.
        self.before_put = lambda: self.sample(1)
        self.assertEqual(await self.advance(), "pressure_high")
        self.assertEqual(self.writes, [])
        self.assertIsNone(self.journal.read())
        self.before_put = None
        self.now = 117
        self.sample(0)
        self.assertEqual(await self.advance(), "pressure_recovering")
        self.now = 122
        self.sample(0)
        self.assertEqual(await self.advance(), "confirmed")
        self.assertEqual(self.writes, [(2, 1)])  # Model 2's floor before extras for model 1.
        for _ in range(3):
            self.assertEqual(await self.advance(), "idle")
        self.assertEqual(self.writes, [(2, 1)])
        self.instances[-1].update(worker_id=2, gpu_indexes=[0], state="running")
        self.models[2]["ready_replicas"] = 1
        self.workers[1]["status"]["gpu_devices"][0]["memory"]["allocated"] = 3
        self.assertEqual(await self.advance(), "confirmed")
        self.assertEqual(len(self.writes), 2)

    async def test_reconcile_merges_three_join_notifications_into_one_burst(self):
        cards = self.cards()
        visible = {"gpu-1": cards["gpu-1"]}
        changed, baseline, planned = asyncio.Event(), asyncio.Event(), asyncio.Event()
        records = []

        async def read():
            baseline.set()
            return {1: visible.copy()}

        async def read_deployments():
            return [SimpleNamespace(**m) for m in self.models.values()], []

        class Capture(logging.Handler):
            def emit(self, record):
                if record.msg.startswith("Replica observe plan"):
                    plan = record.args if isinstance(record.args, dict) else record.args[0]
                    if plan.get("event"):
                        records.append(plan["event"])
                        planned.set()

        logger = logging.getLogger("maas_replica")
        capture, old_level = Capture(), logger.level
        logger.addHandler(capture)
        logger.setLevel(logging.INFO)
        with patch("maas_replica.read_deployments", read_deployments):
            task = asyncio.create_task(reconcile(changed, read, policy_file=SimpleNamespace(
                refresh=lambda: self.policy)))
            try:
                await asyncio.wait_for(baseline.wait(), 2)
                await asyncio.sleep(.05)
                for ident in (2, 3, 4):
                    visible[f"gpu-{ident}"] = cards[f"gpu-{ident}"]
                    changed.set()
                    await asyncio.sleep(.04)
                await asyncio.wait_for(planned.wait(), 3)
                self.assertEqual(records, ["gpu_added_burst"])
            finally:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
                logger.removeHandler(capture)
                logger.setLevel(old_level)
