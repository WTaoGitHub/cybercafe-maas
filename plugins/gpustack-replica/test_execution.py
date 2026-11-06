import asyncio
from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from maas_replica_execution import Action, Deployment, Executor, PendingWrite, Snapshot, inflight


BOUNDS = dict(floor=1, batch_max=1, global_max=4, cluster_max=3)


class ExecutionTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        tmp = TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.journal = PendingWrite(Path(tmp.name) / "pending.json")
        self.snapshot = Snapshot(1, "capacity-a", {
            1: Deployment(1, 2, "deployment-a", 2, (True, False)),
            2: Deployment(2, 2, "deployment-b", 1, ()),
        })
        self.writes = []

    async def read(self):
        return self.snapshot

    def set_model(self, **kwargs):
        self.snapshot = replace(self.snapshot, deployments={
            **self.snapshot.deployments, 1: replace(self.snapshot.deployments[1], **kwargs)
        })

    def action(self, after):
        model = self.snapshot.deployments[1]
        return Action(self.snapshot.revision, self.snapshot.fingerprint, 1, model.cluster_id,
                      model.owner_id, model.binding, model.replicas, after)

    async def write(self, action):
        self.writes.append(action)
        self.set_model(replicas=action.after)

    def executor(self, write=None):
        return Executor(self.journal, self.read, write or self.write, write_timeout=.03)

    async def test_timeout_restart_and_late_write_rebuild_inflight(self):
        action = self.action(3)
        server_can_commit = asyncio.Event()
        committed = asyncio.Event()
        server_tasks = []

        async def delayed_commit(action):
            await server_can_commit.wait()
            await self.write(action)
            committed.set()

        async def timeout_transport(action):
            server_tasks.append(asyncio.create_task(delayed_commit(action)))
            await asyncio.Event().wait()

        try:
            self.assertEqual(await self.executor(timeout_transport).submit(action, **BOUNDS),
                             "unresolved")
            restarted = self.executor()
            self.assertEqual(await restarted.recover(), "unresolved")
            self.assertEqual(inflight(self.snapshot.deployments, 1, self.journal.read()), (3, 2))
            self.assertEqual(await restarted.submit(action, **BOUNDS), "unresolved")
            self.assertEqual(self.writes, [])
            server_can_commit.set()
            await asyncio.wait_for(committed.wait(), 1)
            self.assertEqual(await restarted.recover(), "confirmed")
            self.assertIsNone(self.journal.read())
            self.assertEqual(inflight(self.snapshot.deployments, 1), (3, 2))
            self.set_model(healthy=(True, False, False))
            self.assertEqual(inflight(self.snapshot.deployments, 1), (3, 2))
            self.set_model(healthy=(True, True, False))
            self.assertEqual(inflight(self.snapshot.deployments, 1), (2, 1))
            self.assertEqual(self.writes, [action])
        finally:
            for task in server_tasks:
                task.cancel()
            await asyncio.gather(*server_tasks, return_exceptions=True)

    async def test_timeout_before_commit_stays_fenced_even_with_old_target(self):
        async def timeout_transport(_):
            raise TimeoutError

        action = self.action(3)
        self.assertEqual(await self.executor(timeout_transport).submit(action, **BOUNDS),
                         "unresolved")
        self.assertEqual(await self.executor().recover(), "unresolved")
        self.snapshot = replace(self.snapshot, complete=False)
        self.assertEqual(await self.executor().recover(), "unresolved")
        self.snapshot = replace(self.snapshot, complete=True)
        self.set_model(binding="different")
        self.assertEqual(await self.executor().recover(), "conflict")
        self.assertEqual(self.journal.read(), action)

    async def test_recovery_accepts_only_matching_recovery_binding(self):
        action = replace(self.action(3), recovery_binding="stable")
        self.journal.reserve(action)
        self.set_model(replicas=3, binding="scheduler-change", recovery_binding="other")
        self.assertEqual(await self.executor().recover(), "conflict")
        self.assertEqual(self.journal.read(), action)
        self.set_model(recovery_binding="stable")
        self.assertEqual(await self.executor().recover(), "confirmed")
        self.assertIsNone(self.journal.read())
        self.assertEqual(self.writes, [])

    async def test_native_pending_cannot_be_assumed_first_to_shrink(self):
        self.set_model(replicas=3, healthy=(True, False, False))
        with self.assertRaisesRegex(ValueError, "protected healthy instance"):
            await self.executor().submit(self.action(2), **BOUNDS)
        self.assertEqual(self.writes, [])
        self.assertIsNone(self.journal.read())

    async def test_safe_shrink_waits_for_native_instances_to_disappear(self):
        self.set_model(replicas=3, healthy=(True, True, False))
        action = self.action(2)
        self.assertEqual(await self.executor().submit(action, **BOUNDS), "converging")
        self.assertEqual(await self.executor().recover(), "converging")
        self.set_model(healthy=(True, False))
        self.assertEqual(await self.executor().recover(), "confirmed")
        self.assertEqual(self.writes, [action])

    async def test_explicit_zero_and_default_floor(self):
        self.set_model(replicas=1, healthy=(True,))
        action = self.action(0)
        with self.assertRaisesRegex(ValueError, "scale-down floor"):
            await self.executor().submit(action, **BOUNDS)
        self.assertEqual(await self.executor().submit(action, **(BOUNDS | {"floor": 0})),
                         "converging")
        self.set_model(healthy=())
        self.assertEqual(await self.executor().recover(), "confirmed")

    async def test_stale_plan_and_inflight_budget_block_before_write(self):
        action = self.action(3)
        with self.assertRaisesRegex(ValueError, "budget"):
            await self.executor().submit(action, **(BOUNDS | {"global_max": 2}))
        with self.assertRaisesRegex(ValueError, "budget"):
            await self.executor().submit(action, **(BOUNDS | {"cluster_max": 1}))
        self.snapshot = replace(self.snapshot, revision=2)
        with self.assertRaisesRegex(ValueError, "Stale"):
            await self.executor().submit(action, **BOUNDS)
        self.assertEqual(self.writes, [])
        self.assertIsNone(self.journal.read())
