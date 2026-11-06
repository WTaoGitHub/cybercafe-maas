"""Versioned observe-mode policy and bounded single-GPU replica placement."""
from dataclasses import dataclass
import json
import os
import stat
import time

from maas_replica_pressure import PressureRules


def integer(value, minimum=0):
    if type(value) is not int or value < minimum:
        raise ValueError("Invalid integer policy field")
    return value


@dataclass(frozen=True)
class ModelPolicy:
    vram_claim_bytes: int
    max_replicas: int
    priority: int = 1
    weight: int = 1
    min_replicas: int = 1
    auto_scale_down_min_replicas: int = 1

    def __post_init__(self):
        for value in (self.vram_claim_bytes, self.priority, self.weight):
            integer(value, 1)
        for value in (self.min_replicas, self.auto_scale_down_min_replicas, self.max_replicas):
            integer(value)
            if value > self.max_replicas or value > 32:
                raise ValueError("Invalid replica limit")


EVENTS = {
    "cold_start_empty", "gpu_added", "gpu_added_burst", "gpu_removed",
    "gpu_removed_burst", "capacity_zero", "capacity_restored",
}
MODEL_OVERRIDES = {"priority", "weight", "min_replicas", "max_replicas",
                   "auto_scale_down_min_replicas"}
BATCH_FIELDS = {"scale_up_batch_max", "scale_down_batch_max"}


class Policy:
    def __init__(self, raw, *, allow_apply=False):
        if integer(raw["schema_version"], 1) != 1:
            raise ValueError("Unsupported policy schema")
        self.revision = integer(raw["revision"], 1)
        self.cluster = integer(raw["scope"]["cluster_id"], 1)
        self.owner = integer(raw["scope"]["owner_principal_id"], 1)
        self.models = {int(k): dict(v) for k, v in raw["models"].items()}
        if not self.models or len(self.models) != len(raw["models"]) or len(self.models) > 16:
            raise ValueError("Invalid managed deployment list")
        for key in self.models:
            integer(key, 1)
        self.execution = raw.get("execution", {})
        mode = self.execution.get("mode", "observe")
        if mode not in ("observe", "apply") or (mode == "apply" and not allow_apply):
            raise ValueError("Apply mode requires explicit runtime authorization")
        if mode == "apply" and not {"max_inflight_global", "max_inflight_per_cluster"} <= self.execution.keys():
            raise ValueError("Apply mode requires both in-flight limits")
        supported = BATCH_FIELDS | {"mode", "max_inflight_global", "max_inflight_per_cluster",
                                    "solve_timeout_s"}
        if set(self.execution) - supported:
            raise ValueError("Unsupported execution field in this release")
        for key, value in self.execution.items():
            if key != "mode":
                integer(value, 1 if key == "solve_timeout_s" else 0)
        if self.execution.get("solve_timeout_s", 1) > 2:
            raise ValueError("Planning deadline must be at most two seconds")
        self.events = raw.get("events", {})
        if set(self.events) - {"burst_add_count", "burst_remove_count", "arrival_window_s"}:
            raise ValueError("Unsupported event field in this release")
        self.burst_add = integer(self.events.get("burst_add_count", 3), 2)
        self.burst_remove = integer(self.events.get("burst_remove_count", 3), 2)
        self.arrival_window_s = integer(self.events.get("arrival_window_s", 600), 1)
        if self.arrival_window_s > 3600:
            raise ValueError("Arrival window must not exceed one hour")
        self.pressure = PressureRules.parse(raw["pressure"]) if "pressure" in raw else None
        self.overrides = raw.get("event_overrides", {})
        if set(self.overrides) - EVENTS:
            raise ValueError("Unknown event type")
        self.effective(None)
        for event in self.overrides:
            self.effective(event)

    def effective(self, event):
        override = self.overrides.get(event, {})
        if set(override) - {"models", "execution"}:
            raise ValueError("Unsupported event override field")
        model_overrides = {int(k): v for k, v in override.get("models", {}).items()}
        if set(model_overrides) - self.models.keys() or any(
            set(v) - MODEL_OVERRIDES for v in model_overrides.values()
        ):
            raise ValueError("Event override exceeds managed model fields")
        batch = override.get("execution", {})
        if set(batch) - BATCH_FIELDS:
            raise ValueError("Event cannot modify execution authority or hard limits")
        for key, value in batch.items():
            if integer(value) > self.execution.get(key, 1):
                raise ValueError("Event cannot raise the base batch limit")
        models = {key: ModelPolicy(**(fields | model_overrides.get(key, {})))
                  for key, fields in self.models.items()}
        return models, self.execution | batch


class PolicyFile:
    def __init__(self, path, *, allow_apply=False):
        self.path = path
        self.allow_apply = allow_apply
        self.active = None
        self.content = None

    def refresh(self):
        fd = os.open(self.path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(fd, "rb") as handle:
            mode = os.fstat(handle.fileno()).st_mode
            if not stat.S_ISREG(mode) or mode & 0o077:
                raise ValueError("Policy must be a private regular file")
            data = handle.read(65537)
        if len(data) > 65536:
            raise ValueError("Policy file is too large")
        if data == self.content:
            return self.active
        new = Policy(json.loads(data), allow_apply=self.allow_apply)
        if self.active and new.revision <= self.active.revision:
            raise ValueError("Policy revision must increase")
        self.active, self.content = new, data
        return new


def classify(before, after, policy):
    if before is None:
        return "cold_start_empty" if not after else None
    if not after:
        return "capacity_zero" if before else None
    if not before:
        return "capacity_restored"
    removed = len(before - after)
    added = len(after - before)
    if removed:
        return "gpu_removed_burst" if removed >= policy.burst_remove else "gpu_removed"
    if added:
        return "gpu_added_burst" if added >= policy.burst_add else "gpu_added"
    return None


ARRIVAL_EVENTS = frozenset({"gpu_added", "gpu_added_burst", "capacity_restored"})


def arrival_context(before, after, policy, active=None):
    """Only a confirmed, loss-free device addition opens the phase-one gate."""
    if before is None or not after or before - after:
        return None
    return classify(before, after, policy) or active


def solve(models, budgets, eligible, current, timeout=1, *, additive=False):
    """Plan final per-model counts. Placement is a feasibility proof, not a binding."""
    ids = sorted(models, key=lambda key: (models[key].priority, key))
    tiers = sorted({m.priority for m in models.values()})
    remaining = list(budgets)
    placement = {key: tuple(sorted(eligible[key])) for key in ids}
    if len(budgets) > 64:
        raise ValueError("GPU count exceeds observe-mode solver limit")
    deadline = time.monotonic() + timeout
    weighted = [key for key in ids if models[key].max_replicas]
    weights = sum(models[key].weight for key in weighted)
    counts = {key: current[key] if additive else 0 for key in ids}
    best_score = None
    best = None

    def search(i, start=0):
        nonlocal best, best_score
        if time.monotonic() >= deadline:
            raise TimeoutError("Placement search deadline")
        if i == len(ids):
            coverage = tuple(sum(counts[k] >= models[k].min_replicas > 0
                                 for k in ids if models[k].priority == tier)
                             for tier in tiers)
            total = sum(counts.values())
            weighted_total = sum(counts[key] for key in weighted)
            quota_violations = deviation = 0
            for key in weighted:
                ideal = weighted_total * models[key].weight
                lower, remainder = divmod(ideal, weights)
                upper = lower + bool(remainder)
                quota_violations += max(0, lower - counts[key], counts[key] - upper)
                deviation += abs(counts[key] * weights - ideal)
            delta = sum(abs(counts[k] - current[k]) for k in ids)
            # Allow integer rounding, but do not trade quota compliance for
            # extra replicas of a model that happens to use less VRAM.
            score = coverage, -quota_violations, total, -deviation, -delta
            if best_score is None or score > best_score:
                best_score, best = score, counts.copy()
            return
        key = ids[i]
        model = models[key]
        if counts[key] < model.max_replicas:
            seen = set()
            for at in range(start, len(placement[key])):
                card = placement[key][at]
                equivalent = (remaining[card], tuple(card in placement[k] for k in ids[i + 1:]))
                if remaining[card] < model.vram_claim_bytes or equivalent in seen:
                    continue
                seen.add(equivalent)
                remaining[card] -= model.vram_claim_bytes
                counts[key] += 1
                search(i, at)
                counts[key] -= 1
                remaining[card] += model.vram_claim_bytes
        search(i + 1)

    # Bound recursion even with large budgets and intentionally generous limits.
    if sum(max(0, m.max_replicas - (current[key] if additive else 0))
           for key, m in models.items()) > 96:
        raise ValueError("Total replica limit exceeds observe-mode solver bound")
    search(0)
    return best


def summarize(models, execution, target, current, global_inflight, cluster_inflight):
    effective = {}
    for key, goal in target.items():
        floor = models[key].auto_scale_down_min_replicas
        effective[key] = max(goal, min(current[key], floor))
    up = min(execution.get("scale_up_batch_max", 1),
             max(0, execution.get("max_inflight_global", 1) - global_inflight),
             max(0, execution.get("max_inflight_per_cluster", 1) - cluster_inflight),
             sum(max(0, effective[k] - current[k]) for k in target))
    down = min(execution.get("scale_down_batch_max", 1),
               sum(max(0, current[k] - effective[k]) for k in target))
    return {"current": current, "capacity_target": target, "policy_target": effective,
            "up_preview_limit": up, "down_preview_limit": down}
