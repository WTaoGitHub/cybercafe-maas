import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from maas_replica_pressure import PressureGate, PressureRules


class PressureTests(unittest.TestCase):
    def setUp(self):
        tmp = TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.path = Path(tmp.name) / "sample.json"
        self.now = 100
        self.rules = PressureRules.parse({
            "max_sample_age_s": 10, "recovery_hold_s": 4,
            "signals": {"registry_rejected": {"low": 0, "high": 1},
                        "disk_latency_ms": {"low": 10, "high": 20}},
        })
        self.gate = PressureGate(self.path, clock=lambda: self.now)

    def sample(self, rejected, disk, at=None, worker_ids=None):
        staged = self.path.with_suffix(".new")
        staged.write_text(json.dumps({
            "observed_at": self.now if at is None else at,
            "signals": {"registry_rejected": rejected, "disk_latency_ms": disk},
            **({"worker_ids": worker_ids} if worker_ids is not None else {}),
        }))
        staged.chmod(0o600)
        staged.replace(self.path)

    def test_high_pressure_and_recovery_require_new_low_samples(self):
        self.sample(1, 8)
        self.assertEqual(self.gate.evaluate(self.rules), "pressure_high")
        self.now = 101
        self.sample(0, 8)
        self.assertEqual(self.gate.evaluate(self.rules), "pressure_recovering")
        self.now = 106
        self.assertEqual(self.gate.evaluate(self.rules), "pressure_recovering")
        self.sample(0, 8)
        self.assertEqual(self.gate.evaluate(self.rules), "open")
        self.sample(0, 15)
        self.assertEqual(self.gate.evaluate(self.rules), "open")
        self.sample(0, 20)
        self.assertEqual(self.gate.evaluate(self.rules), "pressure_high")

    def test_stale_missing_and_invalid_samples_fail_closed(self):
        self.assertEqual(self.gate.evaluate(self.rules), "pressure_unavailable")
        self.sample(0, 8, at=89)
        self.assertEqual(self.gate.evaluate(self.rules), "pressure_unavailable")
        self.sample(0, 8, at=101)
        self.assertEqual(self.gate.evaluate(self.rules), "pressure_unavailable")
        self.sample(0, 8)
        self.assertEqual(self.gate.evaluate(self.rules), "pressure_recovering")
        self.path.chmod(0o644)
        self.assertEqual(self.gate.evaluate(self.rules), "pressure_unavailable")
        self.assertEqual(self.gate.evaluate(None), "open")

    def test_gap_in_observations_restarts_recovery_window(self):
        self.sample(0, 8)
        self.assertEqual(self.gate.evaluate(self.rules), "pressure_recovering")
        self.now = 115
        self.sample(0, 8)
        self.assertEqual(self.gate.evaluate(self.rules), "pressure_recovering")
        self.now = 119
        self.sample(0, 8)
        self.assertEqual(self.gate.evaluate(self.rules), "open")

    def test_uncovered_ready_worker_closes_gate_until_fresh_full_coverage(self):
        self.sample(0, 8, worker_ids=[7])
        self.assertEqual(self.gate.evaluate(self.rules, worker_ids={7}), "pressure_recovering")
        self.now = 104
        self.sample(0, 8, worker_ids=[7])
        self.assertEqual(self.gate.evaluate(self.rules, worker_ids={7}), "open")
        self.assertEqual(self.gate.evaluate(self.rules, worker_ids={7, 9}), "pressure_unavailable")
        self.now = 105
        self.sample(0, 8, worker_ids=[7, 9])
        self.assertEqual(self.gate.evaluate(self.rules, worker_ids={7, 9}), "pressure_recovering")
        self.now = 109
        self.sample(0, 8, worker_ids=[7, 9])
        self.assertEqual(self.gate.evaluate(self.rules, worker_ids={7, 9}), "open")

    def test_fresh_out_of_order_sample_restarts_recovery(self):
        self.sample(0, 8)
        self.assertEqual(self.gate.evaluate(self.rules), "pressure_recovering")
        self.now = 104
        self.sample(0, 8)
        self.assertEqual(self.gate.evaluate(self.rules), "open")
        self.now = 105
        self.sample(0, 8, at=103)
        self.assertEqual(self.gate.evaluate(self.rules), "pressure_unavailable")
        self.now = 106
        self.sample(0, 8)
        self.assertEqual(self.gate.evaluate(self.rules), "pressure_recovering")
        self.now = 110
        self.sample(0, 8)
        self.assertEqual(self.gate.evaluate(self.rules), "open")
