from datetime import datetime, timedelta, timezone
from types import SimpleNamespace as Row
import unittest

from maas_retirement_policy import RetirementPolicy, ready_cards

NOW = datetime(2026, 11, 5, tzinfo=timezone.utc)
UUID = "GPU-12345678-1234-1234-1234-123456789abc"


def worker(ident, state="not_ready", age=2700, **overrides):
    fields = dict(id=ident, state=state, heartbeat_time=NOW - timedelta(seconds=age),
                  name=f"e5-{ident:024x}", maintenance=None, external_id=None,
                  unreachable=False, status=Row(gpu_devices=[Row(uuid=UUID)]))
    return Row(**(fields | overrides))


class RetirementTests(unittest.TestCase):
    def test_replacement_requires_continuous_ready_and_fresh_heartbeat(self):
        old, new = worker(1), worker(2, "ready", 30)
        policy = RetirementPolicy(set())
        cards = ready_cards([old, new])
        for tick in range(0, 900, 60):
            policy.observe([old, new], NOW, tick)
            self.assertIsNone(policy.reason(old, cards, NOW, tick))
        policy.observe([old, new], NOW, 900)
        self.assertEqual(policy.reason(old, cards, NOW, 900), "same_gpu_successor")
        self.assertIsNone(policy.reason(old, cards, NOW + timedelta(minutes=3), 901))
        policy.observe([old, new], NOW, 1100)
        self.assertIsNone(policy.reason(old, cards, NOW, 1100))

    def test_long_offline_and_exclusions(self):
        policy = RetirementPolicy({8})
        hour = 3600
        self.assertEqual(policy.reason(worker(1, age=hour), {}, NOW, 0), "long_offline")
        self.assertIsNone(policy.reason(worker(1, age=hour - 1), {}, NOW, 0))
        for excluded in (
            worker(1, age=hour, maintenance=Row(enabled=True)),
            worker(1, age=hour, external_id="external"),
            worker(1, age=hour, name="other"),
            worker(1, age=hour, heartbeat_time=None),
            worker(1, age=hour, state="ready"),
            worker(8, age=hour),
        ):
            self.assertIsNone(policy.reason(excluded, {}, NOW, 0))

    def test_duplicate_ready_owners_block_both_rules(self):
        old = worker(1, age=3600)
        owners = [worker(2, "ready", 30), worker(3, "ready", 30)]
        self.assertIsNone(RetirementPolicy(set()).reason(old, ready_cards(owners), NOW, 0))
