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
        if (not isinstance(raw, dict) or not required <= raw.keys()
                or set(raw) - required - {"require_worker_coverage"}):
            raise ValueError("Invalid pressure policy fields")
        age = number(raw["max_sample_age_s"])
        hold = number(raw["recovery_hold_s"])
        if not age or not isinstance(raw["signals"], dict) or not raw["signals"]:
            raise ValueError("Pressure policy needs a positive sample age and signals")
        signals = []
        for name, threshold in sorted(raw["signals"].items()):
            if (not isinstance(name, str) or not name or not isinstance(threshold, dict)
                    or set(threshold) != {"low", "high"}):
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
        self.diagnostic = {}

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
        self.diagnostic = {
            "state": "pressure_unavailable", "reasons": [], "sample_age_s": None,
            "required_worker_ids": sorted(worker_ids) if worker_ids is not None else None,
            "covered_worker_ids": [], "missing_worker_ids": [],
        }

        def result(state, *reasons):
            self.diagnostic.update(state=state, reasons=list(reasons))
            return state

        if rules != self.rules:
            self.rules = rules
            self.blocked, self.low_since, self.last_sample = True, None, None
        if rules is None:
            return result("open", "pressure_not_configured")
        try:
            if self.path is None:
                raise FileNotFoundError("No pressure feed configured")
            sample = self._sample()
            observed = number(sample["observed_at"])
            age = self.clock() - observed
            self.diagnostic["sample_age_s"] = age if math.isfinite(age) else None
            reasons = []
            if not math.isfinite(age) or age < 0:
                reasons.append("sample_from_future_or_invalid_clock")
            elif age > rules.max_sample_age_s:
                reasons.append("sample_stale")
            if self.last_sample is not None and observed < self.last_sample:
                reasons.append("sample_out_of_order")
            if rules.require_worker_coverage or worker_ids is not None:
                covered = sample["worker_ids"]
                if (worker_ids is None or not isinstance(covered, list)
                        or any(type(ident) is not int or ident < 1 for ident in covered)
                        or len(set(covered)) != len(covered)):
                    raise ValueError("Invalid pressure coverage")
                missing = sorted(worker_ids - set(covered))
                self.diagnostic.update(covered_worker_ids=sorted(covered), missing_worker_ids=missing)
                if missing:
                    reasons.append("missing_workers")
            if reasons:
                self.blocked, self.low_since = True, None
                return result("pressure_unavailable", *reasons)
            values = [(number(sample["signals"][name]), low, high)
                      for name, low, high in rules.signals]
        except (OSError, ValueError, TypeError, KeyError, OverflowError) as exc:
            self.blocked, self.low_since = True, None
            reason = ("sample_missing" if isinstance(exc, FileNotFoundError) else
                      "sample_unreadable" if isinstance(exc, OSError) else "sample_invalid")
            return result("pressure_unavailable", reason)

        if self.last_sample is not None and observed - self.last_sample > rules.max_sample_age_s:
            self.blocked, self.low_since = True, None
        self.last_sample = observed
        if any(value >= high for value, _, high in values):
            self.blocked, self.low_since = True, None
            return result("pressure_high", "high_watermark")
        if not self.blocked:
            return result("open", "within_watermarks")
        if all(value <= low for value, low, _ in values):
            if self.low_since is None:
                self.low_since = observed
            if observed - self.low_since >= rules.recovery_hold_s:
                self.blocked = False
                return result("open", "low_watermark_hold_complete")
        else:
            self.low_since = None
        return result("pressure_recovering", "waiting_for_low_watermark_hold")
