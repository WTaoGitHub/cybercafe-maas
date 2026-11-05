import json
from pathlib import Path
import tempfile
import unittest

from maas_replica_metrics import combine, counter_signals, publish
from maas_replica_pressure import PressureGate, PressureRules


class MetricsTest(unittest.TestCase):
    def test_counters_use_rates_and_reject_reset(self):
        before = (100, 200, 2, 0, 0, 0)
        after = (100 + 2**21, 200 + 2**20, 4, 2**20, 8, 500)
        self.assertEqual(counter_signals(before, after, 2), {
            "network_rx_mib_s": 1, "network_tx_mib_s": .5,
            "disk_write_mib_s": .5, "disk_write_latency_ms": 4, "disk_busy_pct": 25,
        })
        with self.assertRaises(ValueError):
            counter_signals(after, before, 2)

    def test_merged_sample_drives_gate_and_expires_with_oldest_source(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "pressure.json"
            samples = [{"observed_at": 95, "signals": {"disk_busy_pct": 20}},
                       {"observed_at": 98, "signals": {"disk_busy_pct": 10}}]
            publish(path, combine(samples, now=100, max_age=10))
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            self.assertEqual(json.loads(path.read_text()), samples[0])
            rules = PressureRules.parse({"max_sample_age_s": 10, "recovery_hold_s": 0,
                                         "signals": {"disk_busy_pct": {"low": 5, "high": 15}}})
            self.assertEqual(PressureGate(path, clock=lambda: 100).evaluate(rules), "pressure_high")
            self.assertEqual(PressureGate(path, clock=lambda: 106).evaluate(rules), "pressure_unavailable")
            with self.assertRaises(ValueError):
                combine(samples, now=106, max_age=10)
            with self.assertRaises(ValueError):
                combine(samples, now=97, max_age=10)
