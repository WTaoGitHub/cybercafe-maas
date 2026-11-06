import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from maas_replica_policy import Policy, PolicyFile, arrival_context, classify, solve, summarize


def policy_config(revision=1):
    return {
        "schema_version": 1,
        "revision": revision,
        "scope": {"cluster_id": 1, "owner_principal_id": 1},
        "models": {str(i): {"vram_claim_bytes": i, "max_replicas": 3, "weight": i}
                   for i in range(1, 5)},
        "execution": {"scale_up_batch_max": 2, "max_inflight_global": 4},
        "event_overrides": {
            "gpu_added_burst": {"execution": {"scale_up_batch_max": 1}},
            "capacity_zero": {"models": {"4": {"auto_scale_down_min_replicas": 0}}},
        },
    }


class PolicyTests(unittest.TestCase):
    def test_atomic_revision_and_invalid_policy(self):
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "policy.json"
            staging = path.with_suffix(".new")

            def publish(value):
                staging.write_text(json.dumps(value))
                staging.chmod(0o600)
                staging.replace(path)

            publish(policy_config())
            loader = PolicyFile(path)
            self.assertEqual(loader.refresh().revision, 1)
            bad = policy_config(2)
            bad["execution"]["mode"] = "apply"
            publish(bad)
            with self.assertRaises(ValueError):
                loader.refresh()
            self.assertEqual(loader.active.revision, 1)
            publish(policy_config(2))
            self.assertEqual(loader.refresh().revision, 2)

    def test_event_classification_and_nonaccumulating_overlays(self):
        policy = Policy(policy_config())
        self.assertEqual(classify({"a"}, {"a", "b", "c", "d"}, policy), "gpu_added_burst")
        self.assertEqual(classify({"a"}, {"b"}, policy), "gpu_removed")
        self.assertEqual(classify({"a"}, set(), policy), "capacity_zero")
        self.assertEqual(classify(set(), {"a"}, policy), "capacity_restored")
        self.assertEqual(policy.effective("gpu_added_burst")[1]["scale_up_batch_max"], 1)
        self.assertEqual(policy.effective("gpu_removed")[1]["scale_up_batch_max"], 2)

    def test_four_models_and_per_gpu_constraints(self):
        rules, _ = Policy(policy_config()).effective(None)
        current = dict.fromkeys(rules, 0)
        eligible = {1: [0, 1], 2: [0, 1], 3: [0], 4: [1]}
        self.assertEqual(solve(rules, [5, 5], eligible, current), dict.fromkeys(rules, 1))
        result = solve(rules, [3, 3], eligible, current)
        self.assertEqual(result[4], 0)
        self.assertTrue(all(result[key] > 0 for key in (1, 2, 3)))

    def test_priority_and_replica_weight(self):
        config = policy_config()
        config.pop("event_overrides")
        config["models"] = {
            "1": {"vram_claim_bytes": 1, "max_replicas": 3, "weight": 1, "priority": 1},
            "2": {"vram_claim_bytes": 1, "max_replicas": 3, "weight": 2, "priority": 2},
        }
        rules, _ = Policy(config).effective(None)
        self.assertEqual(solve(rules, [1], {1: [0], 2: [0]}, {1: 0, 2: 0}), {1: 1, 2: 0})
        self.assertEqual(solve(rules, [3], {1: [0], 2: [0]}, {1: 0, 2: 0}), {1: 1, 2: 2})

    def test_mixed_vram_arrivals_follow_integer_weight_quotas(self):
        gib = 2**30
        config = policy_config()
        config["models"] = {
            str(key): {"vram_claim_bytes": int(claim * gib), "max_replicas": 10,
                       "weight": weight, "priority": priority}
            for key, claim, weight, priority in ((1, 2, 2, 1), (2, 3, 3, 1), (3, 3.5, 5, 2))
        }
        config.pop("event_overrides")
        rules, _ = Policy(config).effective("gpu_added")

        def plan(free_gib, current):
            eligible = {key: range(len(free_gib)) for key in rules}
            return solve(rules, [int(free * gib) for free in free_gib], eligible, current,
                         additive=True)

        empty = dict.fromkeys(rules, 0)
        self.assertEqual(plan([5], empty), {1: 1, 2: 1, 3: 0})
        self.assertEqual(plan([8.5], empty), dict.fromkeys(rules, 1))
        first = plan([16], empty)
        self.assertEqual(first, {1: 1, 2: 2, 3: 2})
        self.assertEqual(plan([1, 16], first), {1: 2, 2: 3, 3: 5})
        self.assertEqual(plan([16], dict.fromkeys(rules, 1)), {1: 2, 2: 2, 3: 4})
        skewed = {1: 4, 2: 1, 3: 1}
        self.assertEqual(plan([2], skewed), skewed)
        self.assertEqual(plan([3.5], skewed), {1: 4, 2: 2, 3: 1})

    def test_zero_permission_and_batch_budget(self):
        policy = Policy(policy_config())
        rules, execution = policy.effective("capacity_zero")
        current = dict.fromkeys(rules, 2)
        target = solve(rules, [], {key: [] for key in rules}, current)
        self.assertEqual(target, dict.fromkeys(rules, 0))
        plan = summarize(rules, execution, target, current, 0, 0)
        self.assertEqual(plan["policy_target"], {1: 1, 2: 1, 3: 1, 4: 0})
        rules, execution = policy.effective("gpu_added_burst")
        plan = summarize(rules, execution, dict.fromkeys(rules, 3), current, 4, 0)
        self.assertEqual(plan["up_preview_limit"], 0)
        plan = summarize(rules, execution, dict.fromkeys(rules, 3), current, 2, 0)
        self.assertEqual(plan["up_preview_limit"], 1)

    def test_phase_one_only_opens_for_loss_free_gpu_arrivals(self):
        policy = Policy(policy_config())
        self.assertIsNone(arrival_context(None, {"a"}, policy))
        self.assertEqual(arrival_context(set(), {"a"}, policy), "capacity_restored")
        self.assertEqual(arrival_context({"a"}, {"a", "b"}, policy), "gpu_added")
        self.assertEqual(arrival_context({"a"}, {"a", "b", "c", "d"}, policy), "gpu_added_burst")
        self.assertEqual(arrival_context({"a"}, {"a"}, policy, "gpu_added"), "gpu_added")
        self.assertIsNone(arrival_context({"a", "b"}, {"a"}, policy, "gpu_added"))
        self.assertIsNone(arrival_context({"a"}, {"b"}, policy, "gpu_added"))
        self.assertIsNone(arrival_context({"a"}, set(), policy, "gpu_added"))
        self.assertIsNone(arrival_context({"b"}, {"b"}, policy))

    def test_additive_planner_preserves_existing_replicas_even_above_new_cap(self):
        rules, execution = Policy(policy_config()).effective(None)
        current = {1: 4, 2: 1, 3: 0, 4: 0}
        eligible = {1: [0, 1], 2: [0, 1], 3: [0], 4: [1]}
        target = solve(rules, [3, 4], eligible, current, additive=True)
        self.assertEqual(target, {1: 4, 2: 1, 3: 1, 4: 1})
        self.assertEqual(summarize(rules, execution, target, current, 0, 0)["down_preview_limit"], 0)
        self.assertEqual(solve(rules, [0, 0], eligible, current, additive=True), current)

    def test_apply_requires_explicit_authorization_and_both_limits(self):
        config = policy_config()
        config["execution"]["mode"] = "apply"
        with self.assertRaises(ValueError):
            Policy(config)
        with self.assertRaises(ValueError):
            Policy(config, allow_apply=True)
        config["execution"]["max_inflight_per_cluster"] = 1
        self.assertEqual(Policy(config, allow_apply=True).execution["mode"], "apply")
        config["event_overrides"]["gpu_added_burst"]["execution"]["mode"] = "observe"
        with self.assertRaises(ValueError):
            Policy(config, allow_apply=True)
