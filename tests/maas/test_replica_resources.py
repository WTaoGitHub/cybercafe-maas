from copy import deepcopy
from types import SimpleNamespace
import unittest
from dataclasses import replace

from maas_platform.replica_contract import ControlPolicy
from maas_platform.replica_resources import resolve_resources, ResourceDeclarationError
from maas_platform.replica_targets import plan_controlled_capacity, startup_free_bytes
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
                    {'gpu': Device(4, 0, 8, (1 << 30) + 8, 0, 1 << 30, (), 'cuda')}, [row], [])
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
                    {'gpu': Device(4, 0, 20-allocated, (1 << 30) + 20, allocated, 1 << 30, (), 'cuda')}, [row], [instance])

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
                    {'gpu': Device(4, 0, 20, (1 << 30) + 20, 0, 1 << 30, (), 'cuda')}, [row, other], instances)
        self.assertEqual(plan['policy_target'], {1: 3})  # Keep unmanaged 8, managed total <=12.

    def test_per_card_floor_percentage_and_existing_native_reservation(self):
        gib = 1 << 30
        for total, reserved, expected in [
                (8*gib, 0, 7*gib), (gib//2, 0, 0),
                (16*gib, gib, 16*gib - (16*gib+9)//10),
                (16*gib, 2*gib, 14*gib), (16*gib, 20*gib, 0)]:
            with self.subTest(total=total, reserved=reserved):
                card = Device(1, 0, max(0, total-reserved), total, 0, reserved, (), 'cuda')
                self.assertEqual(startup_free_bytes(card), expected)

    def test_4080_super_new_starts_and_existing_tight_layouts(self):
        gib = 1 << 30
        pp, ovis, unlimited = 7*gib//2, 6*gib, 12*gib
        total = 16376 * (1 << 20)
        for parallel in (False, True):
            for existing, incoming, expected_add in [
                    ([pp]*3, pp, 1), ([unlimited], pp, 0), ([pp], unlimited, 0),
                    ([ovis, ovis], pp, 0), ([pp, ovis], ovis, 0),
                    ([pp, unlimited], pp, 0), ([ovis, ovis, pp], ovis, 0)]:
                with self.subTest(parallel=parallel, existing=existing, incoming=incoming):
                    raw = config()
                    raw['startup'] = {'parallel_startup': parallel}
                    raw['models']['1']['max_replicas'] = 8
                    current = existing.count(incoming)
                    models = [SimpleNamespace(**{**model(), 'replicas': current, 'ready_replicas': current,
                                                 'env': {'GPUSTACK_MODEL_VRAM_CLAIM': str(incoming)}})]
                    rows = []
                    for i, amount in enumerate(existing, 2):
                        ident = 1 if amount == incoming else i
                        if ident != 1:
                            models.append(SimpleNamespace(**{**model(), 'id': ident,
                                                             'replicas': 1, 'ready_replicas': 1}))
                        rows.append(SimpleNamespace(id=i, model_id=ident, worker_id=4, state='running',
                                                    gpu_indexes=[0], computed_resource_claim={'vram': {'0': amount}}))
                    # The worker counter is stale; native instance claims still consume capacity.
                    card = Device(4, 0, total, total, 0, 0, (), 'cuda')
                    plan = plan_controlled_capacity(ControlPolicy(raw), {'gpu': card}, models, rows, incremental=True)
                    self.assertEqual(plan['policy_target'][1], current + expected_add)
                    self.assertEqual(plan['startup_order'], [1] * expected_add)
                    self.assertEqual(plan['down_preview_limit'], 0)

    def test_fragmented_free_bytes_cannot_be_pooled_for_one_start(self):
        total, claim = 16376 << 20, 3584 << 20
        row = SimpleNamespace(**{**model(), 'env': {'GPUSTACK_MODEL_VRAM_CLAIM': str(claim)}})
        used = 12 << 30
        first = Device(4, 0, total-used, total, used, 0, (), 'cuda')
        devices = {'a': first, 'b': replace(first, worker_id=5)}
        def plan():
            return plan_controlled_capacity(ControlPolicy(config()), devices, [row], [], incremental=True)
        self.assertGreater(plan()['startup_vram']['free_after_reserve_bytes'], claim)
        self.assertEqual(plan()['startup_order'], [])
        devices['b'] = replace(devices['b'], free_vram=total, allocated_vram=0)
        self.assertEqual(plan()['startup_order'], [1])
