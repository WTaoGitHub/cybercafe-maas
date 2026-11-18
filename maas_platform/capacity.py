"""Model-independent capacity policy and calculation for observed GPUStack topology."""
from collections import Counter, defaultdict
from hashlib import sha256
import json
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, PositiveInt, StringConstraints, model_validator


Name = Annotated[str, StringConstraints(min_length=1, max_length=128, pattern=r'^\S+$')]
Percent = Annotated[int, Field(ge=0, le=100)]


class Pool(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    cluster_id: PositiveInt
    model_id: PositiveInt
    owner_principal_id: PositiveInt
    routes: dict[Name, PositiveInt] = Field(min_length=1)
    gpu_names: list[Annotated[str, StringConstraints(min_length=1)]] = Field(min_length=1)
    slots_per_replica: PositiveInt
    hard_limit: PositiveInt
    queue_limit: PositiveInt
    sync_percent: Percent = 0
    gateway_verified: bool = False


class Policy(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    global_limit: PositiveInt
    sync_percent: Percent
    pools: dict[Name, Pool] = Field(min_length=1)
    # Each layout is an exact, jointly tested set of replicas on one GPU model.
    shared_gpu_layouts: dict[str, list[dict[Name, PositiveInt]]] = Field(default_factory=dict)

    @model_validator(mode='after')
    def unique_bindings(self):
        aliases, routes, models = set(), set(), set()
        for pool in self.pools.values():
            if pool.model_id in models or aliases.intersection(pool.routes) or routes.intersection(pool.routes.values()):
                raise ValueError('An alias, route or deployment cannot be assigned to multiple pools')
            models.add(pool.model_id)
            aliases.update(pool.routes)
            routes.update(pool.routes.values())
        for layouts in self.shared_gpu_layouts.values():
            for layout in layouts:
                if not layout.keys() <= self.pools.keys() or sum(layout.values()) < 2:
                    raise ValueError('Shared GPU layouts require at least two replicas of configured pools')
        return self


def catalog_routes(policy, catalog):
    names = {}
    for pool in policy.pools.values():
        for alias, route_id in pool.routes.items():
            name = catalog.get(alias, {}).get('upstream_model')
            if not isinstance(name, str) or not name or names.get(route_id, name) != name:
                raise ValueError('Capacity route is absent or inconsistent in the model catalog')
            names[route_id] = name
    return names


def policy_hash(policy, names, source):
    return sha256(json.dumps([policy.model_dump(), names, source], sort_keys=True).encode()).hexdigest()


def limits(total, percent):
    reserve = (total * percent + 99) // 100
    return {'total': total, 'sync_reserved': reserve, 'async_limit': total - reserve}


def ready_candidates(policy, names, workers, models, instances, routes, targets):
    """Return instance identities and reasons from a complete native observation."""
    by_route = defaultdict(list)
    for target in targets.values():
        by_route[target['route_id']].append(target)
    by_model = {pool.model_id: key for key, pool in policy.pools.items()}
    devices = {}
    identities = Counter()
    locations = Counter()
    for worker_id, worker in workers.items():
        for gpu in (worker.get('status') or {}).get('gpu_devices') or []:
            location = (worker_id, gpu['index'])
            locations[location] += 1
            uuid = gpu.get('uuid')
            if isinstance(uuid, str) and uuid:
                devices[location] = gpu
                identities[uuid] += 1

    occupants = defaultdict(Counter)
    placements = {}
    for ident, instance in instances.items():
        indexes = instance.get('gpu_indexes') or []
        keys = [(instance.get('worker_id'), index) for index in indexes]
        for key in keys:
            # Count other deployments and starting instances as potential co-tenants.
            occupants[key][by_model.get(instance['model_id'])] += 1
        peers = (instance.get('distributed_servers') or {}).get('subordinate_workers') or []
        for peer in peers:
            for index in peer.get('gpu_indexes') or []:
                occupants[peer.get('worker_id'), index][None] += 1
        if len(keys) == 1 and not peers and instance['state'] == 'running':
            placements[ident] = keys[0]

    candidates = {key: {} for key in policy.pools}
    reasons = {}
    for key, pool in policy.pools.items():
        model = models.get(pool.model_id, {})
        if model.get('cluster_id') != pool.cluster_id or model.get('owner_principal_id') != pool.owner_principal_id or any(
            (routes.get(route_id, {}).get('effective_name') or routes.get(route_id, {}).get('name'))
            != names[route_id] or len(by_route[route_id]) != 1
            or routes[route_id].get('owner_principal_id') != pool.owner_principal_id
            or by_route[route_id][0].get('model_id') != pool.model_id
            or by_route[route_id][0].get('provider_id') is not None
            or by_route[route_id][0].get('overridden_model_name') is not None
            or by_route[route_id][0].get('fallback_status_codes')
            for route_id in pool.routes.values()
        ):
            reasons[key] = 'binding_unverified'
        elif not pool.gateway_verified:
            reasons[key] = 'gateway_unverified'
        elif any(by_route[route_id][0].get('state') != 'active' or by_route[route_id][0].get('weight', 0) <= 0
                 for route_id in pool.routes.values()):
            reasons[key] = 'route_unavailable'
        else:
            reasons[key] = 'no_eligible_replica'

    for ident, location in placements.items():
        instance = instances[ident]
        key = by_model.get(instance['model_id'])
        if key is None or reasons[key] != 'no_eligible_replica':
            continue
        worker = workers.get(location[0], {})
        gpu = devices.get(location)
        if (worker.get('cluster_id') != policy.pools[key].cluster_id or worker.get('state') != 'ready'
                or worker.get('unreachable') or (worker.get('maintenance') or {}).get('enabled')
                or not gpu or gpu.get('name') not in policy.pools[key].gpu_names
                or locations[location] != 1 or identities[gpu['uuid']] != 1):
            continue
        layout = occupants[location]
        if sum(layout.values()) > 1 and layout not in policy.shared_gpu_layouts.get(gpu.get('name'), []):
            continue
        candidates[key][ident] = (location[0], gpu['uuid'], instance.get('created_at'),
                                  instance.get('last_restart_time'), instance.get('restart_count'))
    return candidates, reasons


class Capacity:
    """Shrink on the first complete observation, grow across two distinct readings."""

    def __init__(self, policy, settle, ttl):
        self.policy, self.settle, self.ttl = policy, settle, ttl
        self.observed = None
        self.active = set()
        self.pending = {}

    def interrupted(self):
        self.pending.clear()

    def update(self, observed, candidates, reasons):
        if self.observed is not None:
            if observed <= self.observed:
                raise ValueError('Out-of-order capacity observation')
            if observed - self.observed >= self.ttl:
                self.active.clear()
                self.pending.clear()
        self.observed = observed
        active, pending, pools = set(), {}, {}
        for key, pool in self.policy.pools.items():
            ready = []
            for ident, binding in candidates[key].items():
                marker = (key, ident, binding)
                first = self.pending.get(marker)
                if marker in self.active or (first is not None and observed - first >= self.settle):
                    active.add(marker)
                    ready.append(ident)
                else:
                    pending[marker] = observed if first is None else first
            pools[key] = dict(limits(min(pool.hard_limit, len(ready) * pool.slots_per_replica), pool.sync_percent),
                              aliases=sorted(pool.routes), model_id=pool.model_id, queue_limit=pool.queue_limit,
                              ready_ids=sorted(ready), pending_ids=sorted(candidates[key].keys() - set(ready)),
                              reason='ready' if ready else 'stabilizing' if candidates[key] else reasons[key])
        self.active, self.pending = active, pending
        total = min(self.policy.global_limit, sum(pool['total'] for pool in pools.values()))
        return {'global': limits(total, self.policy.sync_percent), 'pools': pools}
