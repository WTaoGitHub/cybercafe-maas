import unittest

from maas_platform.replica_contract import ControlPolicy
from maas_platform.replica_observe import native_devices
from maas_platform.replica_reconcile import ReconcileObserve
from maas_replica_native import NativeAdapter
from maas_replica_planner import plan_startup_batch
from maas_replica_policy import ModelPolicy
from maas_replica_pressure import PressureGate

from test_replica_reconcile import model, worker


class IncrementalPlanTest(unittest.IsolatedAsyncioTestCase):
    async def test_burst_arrival_loss_and_recovery_at_scale(self):
        model_ids = range(1, 14)
        policy = ControlPolicy({
            'schema_version': 2, 'revision': 1,
            'scope': {'cluster_id': 1, 'owner_principal_id': 1},
            'models': {str(key): {'max_replicas': 32, 'weight': key} for key in model_ids},
            'execution': {'mode': 'observe', 'scale_up_batch_max': 4,
                          'max_inflight_global': 4, 'max_inflight_per_cluster': 4},
            'startup': {'parallel_startup': True},
            'pressure': {'mode': 'disabled'}, 'reconcile': {'stability_s': 0},
        })
        rows = {
            'workers': {},
            'models': {key: {**model(), 'id': key, 'name': f'ocr-{key}', 'replicas': 2,
                             'ready_replicas': 2} for key in model_ids},
            'model-instances': {index: {'id': index, 'model_id': (index + 1) // 2, 'state': 'running',
                                        'worker_id': index, 'gpu_indexes': [0],
                                        'computed_resource_claim': {'vram': {'0': 4}}}
                                for index in range(1, 27)},
        }
        observer = ReconcileObserve(native_devices, PressureGate(None))

        async def listing(_, kind):
            return rows[kind]

        for count in (100, 500, 1000):
            with self.subTest(workers=count):
                rows['workers'] = {key: worker(key) for key in range(1, count + 1)}
                for key in range(1, 27):
                    rows['workers'][key]['status']['gpu_devices'][0]['memory']['allocated'] = 4
                for offline in (False, True, False):
                    for key, row in rows['workers'].items():
                        row['state'] = 'not_ready' if offline and key > 26 else 'ready'
                    status = await observer.tick(None, listing, policy, subscribed=True)
                    self.assertTrue(status['snapshot_complete'])
                    self.assertFalse(status['startup_plan_timed_out'])
                    self.assertEqual(status['eligible_gpu_count'], 26 if offline else count)
                    # Occupied GPUs still have headroom. Protect Ready capacity
                    # while recomputing a fresh, independently bounded batch.
                    self.assertEqual(status['up_preview_limit'], 4)
                    self.assertEqual(status['startup_order'], [13, 12, 11, 10])
                    self.assertTrue(all(status['policy_target'][key] >= 2 for key in model_ids))
                for row in rows['workers'].values():
                    row['state'] = 'not_ready'
                status = await observer.tick(None, listing, policy, subscribed=True)
                self.assertEqual(status['eligible_gpu_count'], 0)
                self.assertEqual(status['startup_order'], [])
                self.assertEqual(status['policy_target'], dict.fromkeys(model_ids, 1))

    def test_partial_batch_respects_fit_and_time_limits(self):
        rules = {1: ModelPolicy(3, 32), 2: ModelPolicy(3, 32)}
        current = {1: 0, 2: 0}
        eligible = {1: [0, 1], 2: [0, 1]}
        self.assertEqual(plan_startup_batch(rules, [3, 3], eligible, current, 4, 1),
                         ({1: 1, 2: 1}, [1, 2], False))
        ticks = iter((0, 0.1, 0.2, 1))
        self.assertEqual(plan_startup_batch(rules, [3, 3], eligible, current, 4, 1,
                                            clock=lambda: next(ticks)),
                         ({1: 1, 2: 0}, [1], True))

    async def test_writer_inventory_reads_1000_ready_workers_and_retained_records(self):
        rows = [worker(key) for key in range(1, 1012)]
        for row in rows[1000:]:
            row['state'] = 'not_ready'

        async def request(method, path, body=None):
            self.assertEqual(method, 'GET')
            page = int(path.rsplit('page=', 1)[1])
            items = rows if path.startswith('/v2/workers?') else []
            return {'items': items[(page - 1)*100:page*100],
                    'pagination': {'page': page, 'total': len(items),
                                   'totalPage': max(1, (len(items) + 99)//100)}}

        native = NativeAdapter(request, revision=1, cluster_id=1, owner_id=1, model_ids=[])
        snapshot = await native.read()
        self.assertEqual(len(snapshot.gpu_inventory[1]), 1000)
