import asyncio
import unittest
from importlib.util import find_spec
from unittest.mock import patch
from types import SimpleNamespace

if find_spec('maas_replica_planner') is None:
    raise unittest.SkipTest('Install the shared planner or add plugins/gpustack-replica to PYTHONPATH')

from maas_platform.replica_observe import native_devices, run
from maas_replica_planner import plan_snapshot, snapshot_devices
from maas_replica_policy import Policy, arrival_context


def worker(ident, uuid, **overrides):
    return {
        'id': ident, 'cluster_id': 1, 'state': 'ready', 'unreachable': False,
        'maintenance': None, 'system_reserved': {'vram': 1}, 'labels': {'pool': 'ocr'},
        'status': {'gpu_devices': [
            {'index': 0, 'uuid': uuid, 'type': 'cuda', 'memory': {'total': 8, 'allocated': 0}}
        ]},
        **overrides,
    }


class ObserveTest(unittest.IsolatedAsyncioTestCase):
    async def test_external_inventory_matches_plugin_plan(self):
        first, second = worker(1, 'a'), worker(2, 'b')
        first['status']['gpu_devices'][0]['memory']['allocated'] = 4
        internal = [
            SimpleNamespace(**{**row, 'system_reserved': SimpleNamespace(vram=1),
                'status': SimpleNamespace(gpu_devices=[
                    SimpleNamespace(**{**d, 'memory': SimpleNamespace(total=d['memory']['total'])})
                    for d in row['status']['gpu_devices']
                ])})
            for row in (first, second)
        ]

        async def native_allocation(ident):
            return SimpleNamespace(vram={0: 4 if ident == 1 else 0})

        plugin = (await snapshot_devices([1], internal, native_allocation))[1]
        candidate = await native_devices(1, {1: first, 2: second})
        self.assertEqual(candidate, plugin)
        policy = Policy({
            'schema_version': 1, 'revision': 1, 'scope': {'cluster_id': 1, 'owner_principal_id': 1},
            'models': {'18': {'max_replicas': 2, 'vram_claim_bytes': 4}},
        })
        rows = [SimpleNamespace(id=18, cluster_id=1, owner_principal_id=1, replicas=1,
                                 ready_replicas=1, cpu_offloading=False, gpu_selector=None,
                                 distributed_inference_across_workers=False, worker_selector={'pool': 'ocr'},
                                 env={'GPUSTACK_MODEL_VRAM_CLAIM': '4'})]
        instances = [SimpleNamespace(state='running', worker_id=1, model_id=18, gpu_indexes=[0])]
        event = arrival_context({('a', 1, 0)}, {('a', 1, 0), ('b', 2, 0)}, policy)
        self.assertIsNone(arrival_context(None, {('a', 1, 0)}, policy))
        expected = plan_snapshot(policy, event, plugin, rows, instances)
        self.assertEqual(expected['policy_target'], {18: 2})
        self.assertEqual(plan_snapshot(policy, event, candidate, rows, instances), expected)

    async def test_incomplete_or_conflicting_devices_block_capacity(self):
        self.assertIsNone(await native_devices(1, {1: worker(1, 'a'), 2: worker(2, 'a')}))
        missing = worker(1, 'a')
        del missing['status']['gpu_devices'][0]['memory']['allocated']
        self.assertIsNone(await native_devices(1, {1: missing}))
        self.assertEqual(await native_devices(1, {1: worker(1, 'a', maintenance={'enabled': True})}), {})

    async def test_reconnect_restarts_observe_baseline(self):
        policy = Policy({
            'schema_version': 1, 'revision': 1, 'scope': {'cluster_id': 1, 'owner_principal_id': 1},
            'models': {'18': {'max_replicas': 2, 'vram_claim_bytes': 4}},
        })
        signals = SimpleNamespace(session='original', changed=asyncio.Event())
        workers = {1: worker(1, 'a')}
        model = SimpleNamespace(id=18, cluster_id=1, owner_principal_id=1, replicas=0,
                                ready_replicas=0, cpu_offloading=False, gpu_selector=None,
                                distributed_inference_across_workers=False, worker_selector={},
                                env={'GPUSTACK_MODEL_VRAM_CLAIM': '4'})

        async def listing(client, kind):
            return {'workers': workers, 'models': {18: vars(model)}, 'model-instances': {}}[kind]

        plans = asyncio.Queue()
        with patch('maas_platform.replica_observe.LOG.info', side_effect=lambda _, plan: plans.put_nowait(plan)):
            task = asyncio.create_task(run(None, listing, signals, SimpleNamespace(refresh=lambda: policy)))
            self.addAsyncCleanup(self.cancel, task)
            self.assertEqual((await asyncio.wait_for(plans.get(), 5))['state'], 'waiting_for_gpu_arrival')
            workers[2] = worker(2, 'b')
            signals.changed.set()
            self.assertGreater((await asyncio.wait_for(plans.get(), 5))['up_preview_limit'], 0)
            signals.session = None
            signals.changed.set()
            self.assertEqual((await asyncio.wait_for(plans.get(), 5))['state'], 'blocked')
            signals.session = 'reconnected'
            signals.changed.set()
            self.assertEqual((await asyncio.wait_for(plans.get(), 5))['state'], 'waiting_for_gpu_arrival')

    @staticmethod
    async def cancel(task):
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
