from copy import deepcopy
import unittest

from maas_replica_execution import WriteNotSent
from maas_platform.replica_apply import MODEL_UPDATE_FIELDS, model_settings
from maas_platform.replica_multi_trial import MultiReplicaRequest, expectations


class OracleTest(unittest.TestCase):
    def test_frozen_plan_has_six_slots_and_explicit_integer_rounding(self):
        claim = 3758096384
        plan = expectations([13413384192, 13413384192], claim)
        self.assertEqual(plan['equal'], [2, 2, 2])
        self.assertEqual(plan['weighted_allowed'], [[3, 2, 1], [3, 1, 2]])
        self.assertEqual(plan['minimum_ready'], [1, 2, 2])

    def test_capacity_change_cannot_silently_change_acceptance_targets(self):
        for budgets in ([1, 1], [20, 20], [12], [12, 12, 12]):
            with self.assertRaises(ValueError):
                expectations(budgets, 4)


class TransportTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.rows = {}
        for i in (5, 6, 7):
            row = {key: None for key in MODEL_UPDATE_FIELDS}
            row.update(id=i, cluster_id=1, owner_principal_id=1, replicas=2,
                       env={'GPUSTACK_MODEL_VRAM_CLAIM': '3758096384'})
            self.rows[i] = row
        self.puts = []; self.records = []; self.minimum = 1
        class Native:
            url = 'http://127.0.0.1:12345'
            async def __call__(inner, method, path, body=None):
                if path.startswith('/v2/models?'):
                    return {'items': deepcopy(list(self.rows.values()))}
                ident = int(path.rsplit('/', 1)[1])
                if method == 'PUT':
                    self.puts.append((ident, deepcopy(body)))
                    self.rows[ident].update(body)
                return deepcopy(self.rows[ident])
        self.transport = MultiReplicaRequest(Native(), {5: 3, 6: 3, 7: 3}, 1, 1,
                           lambda kind, value: self.records.append((kind, value)), lambda _: self.minimum)

    async def test_all_three_models_share_one_scope_and_record_current_vector(self):
        for ident in (5, 6, 7):
            await self.transport('PUT', f'/v2/models/{ident}',
                                 {**model_settings(self.rows[ident]), 'replicas': 3})
        self.assertEqual(len(self.puts), 3)
        self.assertEqual(self.records[0][1]['targets_before'], {5: 2, 6: 2, 7: 2})
        self.assertEqual(self.records[-2][1]['targets_before'], {5: 3, 6: 3, 7: 2})

    async def test_filler_and_formal_deployment_are_never_controller_mutation_targets(self):
        for method, path in [('PUT', '/v2/models/1'), ('PUT', '/v2/models/8'),
                             ('POST', '/v2/models'), ('DELETE', '/v2/models/5')]:
            with self.assertRaises(WriteNotSent):
                await self.transport(method, path, {})
        self.assertEqual(self.puts, [])

    async def test_scale_down_respects_current_nonzero_policy_floor(self):
        self.minimum = 2
        with self.assertRaises(WriteNotSent):
            await self.transport('PUT', '/v2/models/5', {**model_settings(self.rows[5]), 'replicas': 1})
        self.rows[5]['replicas'] = 3
        await self.transport('PUT', '/v2/models/5', {**model_settings(self.rows[5]), 'replicas': 2})
        self.assertEqual(len(self.puts), 1)

    async def test_non_replica_change_or_cap_violation_is_not_sent(self):
        for extra in [{'env': {}}, {'backend_version': 'stale'}, {'replicas': 4}, {'replicas': 0}]:
            with self.assertRaises(WriteNotSent):
                await self.transport('PUT', '/v2/models/5',
                                     {**model_settings(self.rows[5]), 'replicas': 3, **extra})
        self.assertEqual(self.puts, [])

    async def test_changed_native_owner_stops_dispatch(self):
        self.rows[5]['owner_principal_id'] = 2
        with self.assertRaises(WriteNotSent):
            await self.transport('PUT', '/v2/models/5', {**model_settings(self.rows[5]), 'replicas': 3})
        self.assertEqual(self.puts, [])
