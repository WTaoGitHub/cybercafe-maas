"""Fail-closed admission from an optional private resource telemetry feed."""
from dataclasses import dataclass
import json
import math
import os
from pathlib import Path
import stat
import time


def number(value):
    if (type(value) not in (int, float) or value < 0
            or (type(value) is float and not math.isfinite(value))):
        raise ValueError("Pressure values must be finite and nonnegative")
    return value


@dataclass(frozen=True)
class PressureRules:
    max_sample_age_s: float
    recovery_hold_s: float
    signals: tuple
    require_worker_coverage: bool = False

    @classmethod
    def parse(cls, raw):
        required = {"max_sample_age_s", "recovery_hold_s", "signals"}
        if not required <= raw.keys() or set(raw) - required - {"require_worker_coverage"}:
            raise ValueError("Invalid pressure policy fields")
        age = number(raw["max_sample_age_s"])
        hold = number(raw["recovery_hold_s"])
        if not age or not raw["signals"]:
            raise ValueError("Pressure policy needs a positive sample age and signals")
        signals = []
        for name, threshold in sorted(raw["signals"].items()):
            if not name or set(threshold) != {"low", "high"}:
                raise ValueError("Invalid pressure signal")
            low, high = number(threshold["low"]), number(threshold["high"])
            if low >= high:
                raise ValueError("Pressure low watermark must be below high")
            signals.append((name, low, high))
        coverage = raw.get("require_worker_coverage", False)
        if type(coverage) is not bool:
            raise ValueError("Invalid pressure coverage requirement")
        return cls(age, hold, tuple(signals), coverage)


class PressureGate:
    @classmethod
    def from_environment(cls):
        sample = os.environ.get("MAAS_REPLICA_PRESSURE_FILE")
        if sample and not Path(sample).is_absolute():
            raise ValueError("Pressure feed must have an absolute path")
        return cls(sample)

    def __init__(self, path, *, clock=time.time):
        self.path = path
        self.clock = clock
        self.rules = None
        self.blocked = True
        self.low_since = None
        self.last_sample = None

    def _sample(self):
        if self.path is None:
            raise ValueError("Pressure feed is missing")
        fd = os.open(self.path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(fd, "rb") as handle:
            mode = os.fstat(handle.fileno()).st_mode
            if not stat.S_ISREG(mode) or mode & 0o077:
                raise ValueError("Pressure feed must be a private regular file")
            data = handle.read(65537)
        if len(data) > 65536:
            raise ValueError("Pressure sample too large")
        return json.loads(data)

    def evaluate(self, rules, *, worker_ids=None):
        if rules != self.rules:
            self.rules = rules
            self.blocked, self.low_since, self.last_sample = True, None, None
        if rules is None:
            return "open"
        try:
            sample = self._sample()
            observed = number(sample["observed_at"])
            age = self.clock() - observed
            if age < 0 or age > rules.max_sample_age_s or (
                self.last_sample is not None and observed < self.last_sample
            ):
                raise ValueError("Pressure sample is stale or out of order")
            if rules.require_worker_coverage or worker_ids is not None:
                covered = sample["worker_ids"]
                if (worker_ids is None or not isinstance(covered, list)
                        or any(type(ident) is not int or ident < 1 for ident in covered)
                        or not worker_ids <= set(covered)):
                    raise ValueError("Pressure feed does not cover eligible workers")
            values = [(number(sample["signals"][name]), low, high)
                      for name, low, high in rules.signals]
        except (OSError, ValueError, TypeError, KeyError, OverflowError):
            self.blocked, self.low_since = True, None
            return "pressure_unavailable"

        if self.last_sample is not None and observed - self.last_sample > rules.max_sample_age_s:
            self.blocked, self.low_since = True, None
        self.last_sample = observed
        if any(value >= high for value, _, high in values):
            self.blocked, self.low_since = True, None
            return "pressure_high"
        if not self.blocked:
            return "open"
        if all(value <= low for value, low, _ in values):
            if self.low_since is None:
                self.low_since = observed
            if observed - self.low_since >= rules.recovery_hold_s:
                self.blocked = False
                return "open"
        else:
            self.low_since = None
        return "pressure_recovering"
