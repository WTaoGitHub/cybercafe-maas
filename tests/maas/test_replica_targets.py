from types import SimpleNamespace
import unittest

from maas_platform.replica_contract import ControlPolicy
from maas_platform.replica_targets import plan_controlled_capacity
from maas_replica_planner import Device
from test_replica_contract import config
from test_replica_reconcile import model


class TargetsTest(unittest.TestCase):
    def plan(self, *, current=3, total=12, allocated=12, instances=None, extra_models=(), maximum=8):
        raw = config()
        raw['models']['1']['max_replicas'] = maximum
        for row in extra_models:
            raw['models'][str(row.id)] = {'vram_claim_bytes': 4, 'max_replicas': maximum}
        policy = ControlPolicy(raw)
        row = SimpleNamespace(**{**model(), 'replicas': current, 'ready_replicas': current})
        if instances is None:
            instances = [SimpleNamespace(id=i, model_id=1, state='running', worker_id=4,
                                         gpu_indexes=[0]) for i in range(current)]
        card = Device(4, 0, max(0, total-allocated), total, allocated, 0, (), 'cuda')
        return plan_controlled_capacity(policy, {'gpu': card}, [row, *extra_models], instances)

    def test_managed_allocations_are_reclaimed_once_without_reclaiming_unmanaged_bytes(self):
        instances = [SimpleNamespace(id=i, model_id=1, state='running', worker_id=4,
                                     gpu_indexes=[0]) for i in range(2)]
        # 8 bytes belong to managed replicas; 4 remain occupied by another service.
        instances.append(SimpleNamespace(id=10, model_id=2, state='running', worker_id=4, gpu_indexes=[0]))
        extra = SimpleNamespace(**{**model(), 'id': 2, 'replicas': 1, 'ready_replicas': 1})
        raw = config(); raw['models']['1']['max_replicas'] = 8
        plan = plan_controlled_capacity(ControlPolicy(raw),
                    {'gpu': Device(4, 0, 0, 12, 12, 0, (), 'cuda')},
                    [SimpleNamespace(**{**model(), 'replicas': 2, 'ready_replicas': 2}), extra], instances)
        self.assertEqual(plan['capacity_target'], {1: 2})
        self.assertEqual(plan['policy_target'], {1: 2})

    def test_telemetry_lag_cannot_manufacture_reclaimable_memory(self):
        plan = self.plan(current=3, total=8, allocated=0)
        self.assertEqual(plan['capacity_target'], {1: 2})
        self.assertEqual(plan['policy_target'], {1: 2})

    def test_pending_unassigned_over_request_still_has_a_finite_capacity_target(self):
        rows = [SimpleNamespace(id=i, model_id=1, state='pending', worker_id=None,
                                gpu_indexes=None) for i in range(7)]
        plan = self.plan(current=7, total=8, allocated=0, instances=rows)
        self.assertEqual(plan['policy_target'], {1: 2})
        self.assertEqual(plan['up_preview_limit'], 0)
        self.assertEqual(plan['down_preview_limit'], 1)

    def test_unknown_allocation_cannot_justify_resource_driven_shrink(self):
        for change in ({'gpu_indexes': [0, 1]}, {'state': 'error'}, {'deleted_at': 'pending'},
                       {'computed_resource_claim': {'vram': {'0': 8}}}):
            row = SimpleNamespace(id=1, model_id=1, state='running', worker_id=4, gpu_indexes=[0])
            row.__dict__.update(change)
            plan = self.plan(current=3, total=4, allocated=4, instances=[row])
            self.assertEqual(plan['downscale_capacity_proof'], 'allocation_unsettled')
            self.assertEqual(plan['policy_target'], {1: 3})

    def test_explicit_maximum_can_correct_over_request_even_when_allocation_is_unknown(self):
        row = SimpleNamespace(id=1, model_id=1, state='error', worker_id=4, gpu_indexes=[0])
        plan = self.plan(current=3, total=4, allocated=4, instances=[row], maximum=2)
        self.assertEqual(plan['policy_target'], {1: 2})

    def test_multi_model_policy_rebalances_without_hypothetical_repacking_authorizing_growth(self):
        second = SimpleNamespace(**{**model(), 'id': 2})
        plan = self.plan(current=3, total=16, allocated=12, extra_models=[second], maximum=4)
        self.assertEqual(plan['capacity_target'], {1: 2, 2: 2})
        self.assertEqual(plan['policy_target'][1], 2)
        self.assertLessEqual(plan['policy_target'][2], 1)

    def test_absent_or_not_ready_gpu_does_not_contribute_capacity(self):
        rows = [SimpleNamespace(id=1, model_id=1, state='running', worker_id=99, gpu_indexes=[0])]
        plan = self.plan(current=3, total=4, allocated=4, instances=rows)
        self.assertEqual(plan['capacity_target'], {1: 0})
        self.assertEqual(plan['policy_target'], {1: 1})  # Service floor.
