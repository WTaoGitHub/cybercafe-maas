import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from maas_replica_policy import Policy, PolicyFile, classify, solve, summarize


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
