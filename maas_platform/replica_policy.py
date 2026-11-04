"""Bounded placement policy for three independent single-GPU model services."""

from dataclasses import dataclass
from functools import lru_cache
from itertools import product


@dataclass(frozen=True)
class Service:
    claim: int
    priority: int
    weight: int
    limit: int

    def __post_init__(self):
        if any(type(n) is not int or n < 1 for n in
               (self.claim, self.priority, self.weight, self.limit)):
            raise ValueError('Service policy values must be positive integers')


def plan(services, current, free):
    """Choose additive targets using residual per-GPU bytes.

    Already requested replicas must have converged before calling this policy.
    Favor minimum coverage by priority, then capacity and proportionality.
    """
    if len(services) != 3 or len(current) != 3 or len(free) > 2:
        raise ValueError('Pilot requires three services and at most two GPUs')
    if any(type(n) is not int or n < 0 for n in (*current, *free)):
        raise ValueError('Replica counts and GPU budgets must be nonnegative integers')
    limits = tuple(service.limit for service in services)
    if sum(limits) > 32 or any(n > cap for n, cap in zip(current, limits)):
        raise ValueError('Replica counts exceed the bounded pilot policy')
    priorities = sorted({service.priority for service in services})
    claims = tuple(service.claim for service in services)
    weights = tuple(service.weight for service in services)
    weight_sum = sum(weights)

    @lru_cache(maxsize=None)
    def fits(budgets, remaining):
        for i in sorted(range(3), key=lambda i: -claims[i]):
            if not remaining[i]:
                continue
            next_remaining = list(remaining)
            next_remaining[i] -= 1
            for j, budget in enumerate(budgets):
                if budget >= claims[i]:
                    next_budgets = list(budgets)
                    next_budgets[j] -= claims[i]
                    if fits(tuple(sorted(next_budgets)), tuple(next_remaining)):
                        return True
            return False
        return True

    def rank(target):
        coverage = tuple(sum(n > 0 and s.priority == priority
                             for n, s in zip(target, services)) for priority in priorities)
        if 0 in target:
            return coverage, 0, 0, target
        total = sum(target)
        deviation = sum(abs(n * weight_sum - total * w) for n, w in zip(target, weights))
        return coverage, total, -deviation, target

    def candidates():
        for target in product(*(range(n, limit + 1) for n, limit in zip(current, limits))):
            # While any service lacks its first instance, only consider filling floors.
            if 0 in target and any(n > max(1, old) for n, old in zip(target, current)):
                continue
            extra = tuple(n - old for n, old in zip(target, current))
            if fits(tuple(sorted(free)), extra):
                yield target

    return max(candidates(), key=rank)


def floor_recovery_advice(services, running, free):
    """Suggest the smallest same-GPU release to restore one missing service."""
    totals = tuple(sum(counts[i] for counts in running.values()) for i in range(3))
    missing = sorted((i for i, count in enumerate(totals) if count == 0),
                     key=lambda i: services[i].priority)
    for target in missing:
        claim = services[target].claim
        if any(budget >= claim for budget in free.values()):
            # Native reconciliation or additive placement can recover this floor.
            return None
        options = []
        for worker_id, budget in free.items():
            maxima = (min(count, max(0, total - 1))
                      for count, total in zip(running[worker_id], totals))
            for remove in product(*(range(n + 1) for n in maxima)):
                released = sum(n * s.claim for n, s in zip(remove, services))
                if budget + released >= claim:
                    options.append((sum(remove), released, worker_id, remove))
        if options:
            _, _, worker_id, remove = min(options)
            return target, worker_id, remove
    return None
