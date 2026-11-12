from copy import deepcopy
from types import SimpleNamespace
import unittest

from maas_platform.replica_contract import ControlPolicy
from maas_platform.replica_resources import resolve_resources, ResourceDeclarationError
from maas_platform.replica_targets import plan_controlled_capacity
from maas_replica_planner import Device
from test_replica_contract import config
from test_replica_reconcile import model


class ResourcesTest(unittest.TestCase):
    def test_live_claim_overrides_legacy_hint_without_mutating_policy_or_native_model(self):
        raw = config(); before = deepcopy(raw)
        policy = ControlPolicy(raw)
        row = SimpleNamespace(**model()); row.env['GPUSTACK_MODEL_VRAM_CLAIM'] = '8'
        native_before = deepcopy(vars(row))
        live = resolve_resources(policy, [row])
        self.assertIs(live.original, policy)
        self.assertEqual(live.effective(None)[0][1].vram_claim_bytes, 8)
        self.assertEqual(live.models[1]['vram_claim_bytes'], 8)
        self.assertEqual(raw, before)
        self.assertEqual(vars(row), native_before)

    def test_v2_policy_no_longer_needs_a_duplicate_resource_claim(self):
        raw = config(); del raw['models']['1']['vram_claim_bytes']
        live = resolve_resources(ControlPolicy(raw), [SimpleNamespace(**model())])
        self.assertEqual(live.claims, {1: 4})

    def test_numeric_text_normalization_for_planning_does_not_rewrite_native_env(self):
        row = SimpleNamespace(**model()); row.env['GPUSTACK_MODEL_VRAM_CLAIM'] = '0008'
        plan = plan_controlled_capacity(ControlPolicy(config()),
                    {'gpu': Device(4, 0, 8, 8, 0, 0, (), 'cuda')}, [row], [])
        self.assertEqual(plan['policy_target'], {1: 1})
        self.assertEqual(row.env['GPUSTACK_MODEL_VRAM_CLAIM'], '0008')

    def test_missing_or_invalid_live_declaration_never_uses_the_old_hint(self):
        for value in (None, '', '0', '-1', '4.5', True, 4, 4.5, ' 4', 'x'*100, str(2**63)):
            row = SimpleNamespace(**model()); row.env['GPUSTACK_MODEL_VRAM_CLAIM'] = value
            with self.assertRaises(ResourceDeclarationError):
                resolve_resources(ControlPolicy(config()), [row])
        row.env.clear()
        with self.assertRaisesRegex(ResourceDeclarationError, 'resource_claim_missing'):
            resolve_resources(ControlPolicy(config()), [row])

    def mixed(self, old, new, *, declared=True, telemetry=None):
        raw = config(); raw['models']['1']['max_replicas'] = 8
        row = SimpleNamespace(**{**model(), 'replicas': 1, 'ready_replicas': 1,
                                 'env': {'GPUSTACK_MODEL_VRAM_CLAIM': str(new)}})
        instance = SimpleNamespace(id=1, model_id=1, state='running', worker_id=4, gpu_indexes=[0])
        if declared:
            instance.computed_resource_claim = {'vram': {'0': old}}
        allocated = old if telemetry is None else telemetry
        return plan_controlled_capacity(ControlPolicy(raw),
                    {'gpu': Device(4, 0, 20-allocated, 20, allocated, 0, (), 'cuda')}, [row], [instance])

    def test_old_smaller_instance_keeps_actual_cost_and_new_starts_use_larger_claim(self):
        plan = self.mixed(4, 8)
        self.assertEqual(plan['policy_target'], {1: 3})  # old 4 + two NEW 8 = 20.
        self.assertEqual(plan['vram_claim_bytes'], {1: 8})
        self.assertEqual(plan['downscale_capacity_proof'], 'mixed_resource_claims')

    def test_old_larger_instance_does_not_release_memory_merely_because_new_claim_is_smaller(self):
        plan = self.mixed(8, 4)
        self.assertEqual(plan['policy_target'], {1: 4})  # old 8 + three NEW 4 = 20.
        self.assertEqual(plan['downscale_capacity_proof'], 'mixed_resource_claims')

    def test_missing_old_instance_claim_does_not_freeze_proven_new_starts(self):
        plan = self.mixed(8, 4, declared=False)
        self.assertEqual(plan['policy_target'], {1: 4})
        self.assertEqual(plan['downscale_capacity_proof'], 'runtime_claim_unavailable')

    def test_delayed_worker_allocation_does_not_release_old_instance_resource(self):
        plan = self.mixed(8, 4, telemetry=0)
        self.assertEqual(plan['policy_target'], {1: 4})

    def test_unmanaged_native_claim_is_reserved_even_before_worker_counter_catches_up(self):
        raw = config(); raw['models']['1']['max_replicas'] = 8
        row = SimpleNamespace(**{**model(), 'replicas': 1, 'ready_replicas': 1})
        other = SimpleNamespace(**{**model(), 'id': 2, 'replicas': 1, 'ready_replicas': 1})
        instances = [SimpleNamespace(id=i, model_id=i, state='running', worker_id=4,
                                     gpu_indexes=[0], computed_resource_claim={'vram': {'0': amount}})
                     for i, amount in [(1, 4), (2, 8)]]
        plan = plan_controlled_capacity(ControlPolicy(raw),
                    {'gpu': Device(4, 0, 20, 20, 0, 0, (), 'cuda')}, [row, other], instances)
        self.assertEqual(plan['policy_target'], {1: 3})  # Keep unmanaged 8, managed total <=12.
