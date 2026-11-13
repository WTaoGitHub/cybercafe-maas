from copy import deepcopy
import unittest

from maas_replica_execution import WriteNotSent
from maas_platform.replica_apply import MODEL_UPDATE_FIELDS, model_settings
from maas_platform.replica_startup_trial import round_order, startup_policy, startup_counts, StartupRequest


class PlanTest(unittest.TestCase):
    def test_three_repeats_balanced_order_and_fixed_policy(self):
        order = round_order()
        self.assertEqual(order, [(1, 1), (1, 2), (1, 4), (2, 2), (2, 4), (2, 1),
                                (3, 4), (3, 1), (3, 2)])
        for limit in (1, 2, 4):
            self.assertEqual(sum(n == limit for _, n in order), 3)
            raw = startup_policy([5, 6, 7], 1, 1, limit)
            self.assertEqual(raw['startup']['parallel_startup'], limit > 1)
            self.assertEqual(raw['execution']['max_inflight_global'], limit)
            self.assertEqual({r['max_replicas'] for r in raw['models'].values()}, {2})

    def test_invalid_scope_and_limit_refused(self):
        for ids, limit in [([5, 5, 7], 1), ([5, 6], 2), ([5, 6, 7], True), ([5, 6, 7], 8)]:
            with self.assertRaises(ValueError):
                startup_policy(ids, 1, 1, limit)

    def test_audit_counts_missing_instances_surplus_and_other_cluster(self):
        models = [{'id': 1, 'replicas': 2, 'ready_replicas': 2, 'cluster_id': 1},
                  {'id': 5, 'replicas': 2, 'ready_replicas': 1, 'cluster_id': 1},
                  {'id': 9, 'replicas': 1, 'ready_replicas': 0, 'cluster_id': 2}]
        rows = [{'model_id': 1, 'state': 'running'}] * 2 + [{'model_id': 5, 'state': 'running'}]
        self.assertEqual(startup_counts(models, rows, 1), {'global': 2, 'cluster': 1})
        rows += [{'model_id': 5, 'state': 'starting'}] * 2
        self.assertEqual(startup_counts(models, rows, 1), {'global': 3, 'cluster': 2})


class GuardTest(unittest.IsolatedAsyncioTestCase):
    async def test_growth_only_exact_configuration_and_cap_two(self):
        rows = {}
        for i in (5, 6, 7):
            rows[i] = {k: None for k in MODEL_UPDATE_FIELDS}
            rows[i].update(id=i, replicas=0, ready_replicas=0, cluster_id=1,
                           owner_principal_id=1, env={})
        puts = []; records = []
        class Native:
            url = 'http://127.0.0.1:12345'
            async def __call__(self, method, path, body=None):
                if path.startswith('/v2/models?'):return {'items': deepcopy(list(rows.values()))}
                if path.startswith('/v2/model-instances?'):return {'items': []}
                i = int(path.rsplit('/', 1)[1])
                if method == 'PUT':puts.append(body); rows[i].update(body)
                return deepcopy(rows[i])
        request = StartupRequest(Native(), {i: 3 for i in rows}, 1, 1,
                                 lambda k, v: records.append((k, v)), lambda _: 1)
        for target in (1, 2):
            await request('PUT', '/v2/models/5', {**model_settings(rows[5]), 'replicas': target})
        for path, extra in [('/v2/models/5', {'replicas': 3}),
                            ('/v2/models/5', {'replicas': 1}),
                            ('/v2/models/6', {'replicas': 1, 'env': {'stale': 'value'}}),
                            ('/v2/models/1', {'replicas': 1})]:
            with self.assertRaises(WriteNotSent):
                await request('PUT', path, {**model_settings(rows[5]), **extra})
        self.assertEqual(len(puts), 2)
        self.assertEqual(sum(k == 'dispatch' for k, _ in records), 2)
