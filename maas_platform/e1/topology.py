"""Verify independently pinned single-worker aliases, without weakening P4 pins."""
from dataclasses import asdict, dataclass
import math
import time

from ..errors import ServiceError
from ..versioning import verify_pin


@dataclass(frozen=True)
class Replica:
    logical_model: str
    revision: str
    worker: str
    instance_id: int
    route: str
    image_id: str
    slots: int
    observed_at: float
    configuration_sha256: str

    def public(self):
        return asdict(self)


def fresh(observed_at, now, max_age=30):
    return (isinstance(observed_at, (int, float)) and math.isfinite(observed_at)
            and 0 <= now-observed_at <= max_age)


def verify_replica(logical_model, revision, pin, control, inventory, gateway,
                   slots=1, now=None):
    """Inputs must come from the operator collector, never request headers.

    A new alias has its own immutable configuration pin and exactly one worker.
    Native generic routes / X-GPUStack-Model-Instance are NOT a pinning API.
    Deployment verification must observe the actual gateway destinations/policy.
    """
    now = time.time() if now is None else now
    if type(slots) is not int or not 1 <= slots <= 128:
        raise ServiceError('E1_CAPACITY_INVALID', 'Approve a bounded replica slot budget', 409)
    if pin.revision != revision or len(pin.worker_names) != 1:
        raise ServiceError('E1_ROUTE_NOT_PINNED', 'A separate single-worker alias pin is required', 409)
    worker = pin.worker_names[0]
    verify_pin(pin, control['version'], control['route'], control['targets'],
               control['model'], control['backend'], inventory, now=now, max_inventory_age=30)
    # worker_selector is part of the immutable configuration hash. Exact labels
    # need a unique matching worker, so a replacement cannot land elsewhere.
    selector = control['model'].get('worker_selector')
    workers = control['workers']
    matches = [w for w in workers if selector and all(w.get('labels', {}).get(k) == v
                                                     for k, v in selector.items())]
    if (len(matches) != 1 or matches[0]['name'] != worker
            or matches[0].get('state') != 'ready' or matches[0].get('unreachable')
            or (matches[0].get('maintenance') or {}).get('enabled')):
        raise ServiceError('E1_WORKER_UNAVAILABLE', 'Alias placement is not uniquely ready', 503)
    instances = control['instances']
    if (control['model'].get('replicas') != 1 or len(instances) != 1
            or instances[0].get('worker_name') != worker or instances[0].get('state') != 'running'
            or instances[0].get('worker_id') != matches[0].get('id')
            or control['model'].get('distributed_inference_across_workers')):
        raise ServiceError('E1_ROUTE_NOT_PINNED', 'Exactly one local running instance is required', 503)
    instance = instances[0]
    row = inventory[worker]
    if (row.get('worker') != worker or row.get('running_image_ids') != [pin.image_id]
            or row.get('paused') is not False):
        raise ServiceError('MODEL_IMAGE_CHANGED', 'Runtime image inventory is not approved', 409)
    # This is observed gateway evidence, not a caller-provided desired setting.
    # Disabled retries plus exact destination scope are required before sends.
    if (not fresh(gateway.get('observed_at'), now)
            or gateway.get('route_id') != pin.route_id
            or gateway.get('instance_ids') != [instance['id']]
            or gateway.get('max_attempts') != 1
            or gateway.get('policies', {}).get('higress.io/proxy-next-upstream') != 'off'):
        raise ServiceError('E1_GATEWAY_UNVERIFIED', 'Fresh single-destination no-retry proof is required', 503)
    return Replica(logical_model, revision, worker, instance['id'], pin.route_name,
                   pin.image_id, slots, min(gateway['observed_at'], row['observed_at']),
                   pin.configuration_sha256)


def collect_subset(specs, observe, now=None):
    """A failed alias revokes that alias only; no previous proof is reused.

    specs: trusted {logical_model, revision, pin, slots}; observe is injected so
    a dead worker can be bounded independently instead of blocking all models.
    """
    replicas, failures = [], []
    for spec in specs:
        try:
            control, inventory, gateway = observe(spec)
            replicas.append(verify_replica(spec['logical_model'], spec['revision'], spec['pin'],
                control, inventory, gateway, spec.get('slots', 1), now))
        except Exception as exc:
            failures.append({'logical_model': spec['logical_model'], 'route': spec['pin'].route_name,
                'code': exc.code if isinstance(exc, ServiceError) else 'E1_COLLECTION_UNAVAILABLE'})
    return replicas, failures
