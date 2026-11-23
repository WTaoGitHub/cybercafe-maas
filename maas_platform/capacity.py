"""Model-independent capacity policy and calculation for observed GPUStack topology."""
from collections import Counter, defaultdict
from hashlib import sha256
import json
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, PositiveInt, StringConstraints, model_validator

from .replica_resources import positive_bytes


Name = Annotated[str, StringConstraints(min_length=1, max_length=128, pattern=r'^\S+$')]
Percent = Annotated[int, Field(ge=0, le=100)]
GPU = Annotated[str, StringConstraints(pattern=r'^GPU-[0-9a-f]{8}(-[0-9a-f]{4}){3}-[0-9a-f]{12}$')]


class ReplicaRoute(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    route_id: PositiveInt
    model_id: PositiveInt
    gpu_uuid: GPU


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
    replica_routes: dict[Name, ReplicaRoute] = Field(default_factory=dict)
    shared_gpu_vram_bytes: PositiveInt | None = None

    @model_validator(mode='after')
    def unique_pins(self):
        pins = list(self.replica_routes.values())
        if pins and (not self.gateway_verified or self.model_id not in {pin.model_id for pin in pins}
                     or len({pin.route_id for pin in pins}) != len(pins)
                     or len({pin.model_id for pin in pins}) != len(pins)
                     or len({pin.gpu_uuid for pin in pins}) != len(pins)):
            raise ValueError('GPU isolation requires verified, distinct deployments, routes and GPUs')
        return self


class Policy(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    global_limit: PositiveInt
    sync_percent: Percent
    pools: dict[Name, Pool] = Field(min_length=1)
    # Each layout is an exact, jointly tested set of replicas on one GPU model.
    shared_gpu_layouts: dict[str, list[dict[Name, PositiveInt]]] = Field(default_factory=dict)
    shared_gpu_vram_budgets: dict[str, PositiveInt] = Field(default_factory=dict)

    @model_validator(mode='after')
    def unique_bindings(self):
        aliases, routes, models, pinned_names = set(), set(), set(), set()
        for pool in self.pools.values():
            deployments = {pool.model_id, *(pin.model_id for pin in pool.replica_routes.values())}
            route_ids = {*pool.routes.values(), *(pin.route_id for pin in pool.replica_routes.values())}
            if (models.intersection(deployments) or aliases.intersection(pool.routes) or routes.intersection(route_ids)
                    or pinned_names.intersection(pool.replica_routes)):
                raise ValueError('An alias, route or deployment cannot be assigned to multiple pools')
            models.update(deployments)
            aliases.update(pool.routes)
            routes.update(route_ids)
            pinned_names.update(pool.replica_routes)
        for layouts in self.shared_gpu_layouts.values():
            for layout in layouts:
                if not layout.keys() <= self.pools.keys() or sum(layout.values()) < 2:
                    raise ValueError('Shared GPU layouts require at least two replicas of configured pools')
        return self


def catalog_routes(policy, catalog):
    names = {}
    for pool in policy.pools.values():
        for alias, route_id in pool.routes.items():
            model = catalog.get(alias, {})
            name = model.get('upstream_model')
            if not isinstance(name, str) or not name or names.get(route_id, name) != name:
                raise ValueError('Capacity route is absent or inconsistent in the model catalog')
            if pool.replica_routes and model.get('execution_guard') is not True:
                raise ValueError('GPU isolation requires execution guards on every logical alias')
            names[route_id] = name
    for pool in policy.pools.values():
        for name, pin in pool.replica_routes.items():
            if names.get(pin.route_id, name) != name or any(
                    other_id != pin.route_id and upstream == name for other_id, upstream in names.items()):
                raise ValueError('Pinned route conflicts with the model catalog')
            names[pin.route_id] = name
    return names


def policy_hash(policy, names, source):
    encoded = policy.model_dump()
    if not encoded['shared_gpu_vram_budgets']:
        del encoded['shared_gpu_vram_budgets']
    for pool in encoded['pools'].values():
        if not pool['replica_routes']:
            del pool['replica_routes']
        if pool['shared_gpu_vram_bytes'] is None:
            del pool['shared_gpu_vram_bytes']
    return sha256(json.dumps([encoded, names, source], sort_keys=True).encode()).hexdigest()


def limits(total, percent):
    reserve = (total * percent + 99) // 100
    return {'total': total, 'sync_reserved': reserve, 'async_limit': total - reserve}


def route_state(route_id, model_id, pool, names, routes, by_route):
    route = routes.get(route_id, {})
    targets = by_route[route_id]
    if ((route.get('effective_name') or route.get('name')) != names[route_id]
            or route.get('owner_principal_id') != pool.owner_principal_id or len(targets) != 1):
        return 'binding_unverified'
    target = targets[0]
    if (target.get('model_id') != model_id or target.get('provider_id') is not None
            or target.get('overridden_model_name') is not None or target.get('fallback_status_codes')):
        return 'binding_unverified'
    if target.get('state') != 'active' or target.get('weight', 0) <= 0:
        return 'route_unavailable'
    return 'no_eligible_replica'


def fits_gpu_vram(policy, gpu, worker, occupants):
    """Check the full workload on the card, including instances still starting."""
    name, index = gpu['name'], gpu['index']
    memory, reserved = gpu.get('memory'), worker.get('system_reserved')
    try:
        available = positive_bytes(memory['total'])
        reserve = reserved['vram']
        if type(reserve) is not int or reserve < 0:
            return False
        budget = min(policy.shared_gpu_vram_budgets[name], available - reserve)
        allocated = 0
        for key, instance in occupants:
            pool = policy.pools.get(key)
            if (not pool or not pool.shared_gpu_vram_bytes or name not in pool.gpu_names
                    or instance.get('gpu_indexes') != [index]
                    or (instance.get('distributed_servers') or {}).get('subordinate_workers')):
                return False
            claims = instance['computed_resource_claim']['vram']
            if not isinstance(claims, dict) or len(claims) != 1:
                return False
            allocated += max(pool.shared_gpu_vram_bytes,
                             positive_bytes(claims.get(str(index), claims.get(index))))
        return allocated <= budget
    except (KeyError, TypeError, ValueError):
        return False


def ready_candidates(policy, names, workers, models, instances, routes, targets):
    """Return instance identities and reasons from a complete native observation."""
    by_route = defaultdict(list)
    for target in targets.values():
        by_route[target['route_id']].append(target)
    by_model = {}
    pins = {}
    for key, pool in policy.pools.items():
        by_model[pool.model_id] = key
        for route, pin in pool.replica_routes.items():
            by_model[pin.model_id] = key
            pins[pin.model_id] = (route, pin)
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

    occupants = defaultdict(list)
    replicas = Counter()
    placements = {}
    for ident, instance in instances.items():
        replicas[instance['model_id']] += 1
        indexes = instance.get('gpu_indexes') or []
        keys = [(instance.get('worker_id'), index) for index in indexes]
        for key in keys:
            # Count other deployments and starting instances as potential co-tenants.
            occupants[key].append((by_model.get(instance['model_id']), instance))
        peers = (instance.get('distributed_servers') or {}).get('subordinate_workers') or []
        for peer in peers:
            for index in peer.get('gpu_indexes') or []:
                occupants[peer.get('worker_id'), index].append((None, peer))
        if len(keys) == 1 and not peers and instance['state'] == 'running':
            placements[ident] = keys[0]

    candidates = {key: {} for key in policy.pools}
    reasons = {}
    for key, pool in policy.pools.items():
        model = models.get(pool.model_id, {})
        if model.get('cluster_id') != pool.cluster_id or model.get('owner_principal_id') != pool.owner_principal_id:
            reasons[key] = 'binding_unverified'
        else:
            states = set() if pool.replica_routes else {
                route_state(route_id, pool.model_id, pool, names, routes, by_route)
                for route_id in pool.routes.values()
            }
            reasons[key] = next((reason for reason, applies in (
                ('binding_unverified', 'binding_unverified' in states),
                ('gateway_unverified', not pool.gateway_verified),
                ('route_unavailable', 'route_unavailable' in states),
            ) if applies), 'no_eligible_replica')

    card_fits, blocked_routes = {}, set()
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
        assigned = occupants[location]
        name = gpu['name']
        if name in policy.shared_gpu_vram_budgets:
            if location not in card_fits:
                card_fits[location] = fits_gpu_vram(policy, gpu, worker, assigned)
            if not card_fits[location]:
                # A pooled route can still reach this instance on the rejected card.
                if not policy.pools[key].replica_routes:
                    blocked_routes.add(key)
                continue
        elif len(assigned) > 1 and Counter(pool for pool, _ in assigned) not in policy.shared_gpu_layouts.get(name, []):
            continue
        binding = (location[0], gpu['uuid'], instance.get('created_at'),
                   instance.get('last_restart_time'), instance.get('restart_count'))
        if instance['model_id'] in pins:
            route, pin = pins[instance['model_id']]
            pool = policy.pools[key]
            model = models.get(pin.model_id, {})
            canonical = models[pool.model_id]
            selector = model.get('gpu_selector') or {}
            if (gpu['uuid'] != pin.gpu_uuid or replicas[pin.model_id] != 1
                    or model.get('replicas') != 1 or model.get('distributed_inference_across_workers')
                    or model.get('generic_proxy') or routes.get(pin.route_id, {}).get('generic_proxy')
                    or model.get('cluster_id') != pool.cluster_id
                    or model.get('owner_principal_id') != pool.owner_principal_id
                    or selector.get('gpus_per_replica') != 1
                    or selector.get('gpu_ids') != [f"{worker.get('name')}:{gpu.get('type')}:{location[1]}"]
                    or any(model.get(field) != canonical.get(field) for field in
                           ('source', 'local_path', 'huggingface_repo_id', 'huggingface_filename',
                            'model_scope_model_id', 'model_scope_file_path', 'backend', 'backend_version',
                            'backend_parameters', 'image_name', 'run_command'))
                    or route_state(pin.route_id, pin.model_id, pool, names, routes, by_route) != 'no_eligible_replica'):
                continue
            binding += (route,)
        candidates[key][ident] = binding
    for key in blocked_routes:
        candidates[key].clear()
        reasons[key] = 'gpu_budget_unverified'
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
            if pool.replica_routes:
                pools[key]['replica_routes'] = {candidates[key][ident][-1]: candidates[key][ident][1] for ident in ready}
        self.active, self.pending = active, pending
        total = min(self.policy.global_limit, sum(pool['total'] for pool in pools.values()))
        return {'global': limits(total, self.policy.sync_percent), 'pools': pools}
