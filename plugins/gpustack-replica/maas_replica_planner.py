"""Replica planning shared by the GPUStack plugin and MaaS."""
from dataclasses import dataclass

from maas_replica_policy import ARRIVAL_EVENTS, solve, summarize


@dataclass(frozen=True)
class Device:
    worker_id: int
    index: int
    free_vram: int
    total_vram: int
    allocated_vram: int
    reserved_vram: int
    labels: tuple
    kind: str


async def snapshot_devices(cluster_ids, workers, allocated):
    clusters = {ident: {} for ident in cluster_ids}
    unknown = set()
    owners = {}
    for worker in workers:
        cluster = worker.cluster_id
        if cluster not in clusters or worker.state != "ready" or worker.unreachable:
            continue
        if worker.maintenance and worker.maintenance.enabled:
            continue
        if worker.status is None or worker.status.gpu_devices is None:
            unknown.add(cluster)
            continue
        if not worker.status.gpu_devices:
            continue
        reserved = getattr(worker.system_reserved, "vram", None)
        usage = await allocated(worker.id)
        indexes = set()
        for device in worker.status.gpu_devices:
            uuid, index = device.uuid, device.index
            if not uuid:
                unknown.add(cluster)
                continue
            if uuid in owners:
                unknown.update((cluster, owners[uuid]))
            owners[uuid] = cluster
            total = getattr(device.memory, "total", None)
            used = usage.vram.get(index, 0)
            if (index in indexes or any(type(value) is not int or value < 0
                                       for value in (index, total, used, reserved))):
                unknown.add(cluster)
                continue
            indexes.add(index)
            clusters[cluster][uuid] = Device(
                worker.id, index, max(0, total - used - reserved), total, used, reserved,
                tuple(sorted((worker.labels or {}).items())), device.type)
    return {ident: None if ident in unknown else devices for ident, devices in clusters.items()}


def plan_snapshot(policy, event, devices, model_rows, instance_rows):
    if event not in ARRIVAL_EVENTS:
        raise ValueError("Phase one requires a confirmed GPU arrival")
    return plan_capacity(policy, devices, model_rows, instance_rows, event=event)


def plan_capacity(policy, devices, model_rows, instance_rows, *, event=None, parallel=False):
    """Pure current-capacity preview; no event or execution authority is implied."""
    rules, execution = policy.effective(event)
    models = {model.id: model for model in model_rows if model.id in rules}
    if models.keys() != rules.keys():
        raise ValueError("Managed deployment missing from snapshot")
    if any(m.cluster_id != policy.cluster or m.owner_principal_id != policy.owner
           for m in models.values()):
        raise ValueError("Managed deployment scope mismatch")
    cards = sorted(devices.values(), key=lambda card: (card.worker_id, card.index))
    indexes = {(card.worker_id, card.index): i for i, card in enumerate(cards)}
    eligible = {}
    for key, model in models.items():
        if (model.cpu_offloading or model.distributed_inference_across_workers
                or model.gpu_selector or getattr(model, "gpu_type_selector", None)
                or str((model.env or {}).get("GPUSTACK_MODEL_VRAM_CLAIM"))
                != str(rules[key].vram_claim_bytes)):
            raise ValueError("Unsupported placement or GPU claim mismatch")
        selector = model.worker_selector or {}
        eligible[key] = [i for i, card in enumerate(cards)
                         if card.kind == "cuda" and all(dict(card.labels).get(k) == v
                                                        for k, v in selector.items())]
    running = {}
    for instance in instance_rows:
        if instance.state == "running" and instance.gpu_indexes and all(
            (instance.worker_id, index) in indexes for index in instance.gpu_indexes
        ):
            running[instance.model_id] = running.get(instance.model_id, 0) + 1
    current = {key: model.replicas for key, model in models.items()}
    budgets = [card.free_vram for card in cards]
    reservation_reason = 'not_enabled'
    if parallel:
        budgets, reservation_reason = startup_reservations(
            policy.cluster, cards, model_rows, instance_rows)
    # Keep all current allocations in place. Native pending replicas have
    # first use of the new capacity before we request any additional slots.
    if ((parallel and reservation_reason != 'reserved') or
            (not parallel and any(running.get(key, 0) < count for key, count in current.items()))):
        target = current.copy()
        waiting = True
    else:
        target = solve(rules, budgets, eligible, current,
                       execution.get("solve_timeout_s", 1), additive=True)
        waiting = False
    if parallel:
        visible = {m.id: sum(row.model_id == m.id for row in instance_rows) for m in model_rows}
        healthy = {m.id: sum(row.model_id == m.id and row.state == 'running'
                            for row in instance_rows) for m in model_rows}
        global_inflight = sum(max(model.replicas, visible[model.id]) -
                              min(model.ready_replicas, healthy[model.id]) for model in model_rows)
        cluster_inflight = sum(max(model.replicas, visible[model.id]) - running.get(model.id, 0)
                               for model in model_rows if model.cluster_id == policy.cluster)
    else:
        # Preserve the schema-v1/plugin preview contract.
        global_inflight = sum(max(0, model.replicas - model.ready_replicas) for model in model_rows)
        cluster_inflight = sum(max(0, model.replicas - running.get(model.id, 0))
                               for model in model_rows if model.cluster_id == policy.cluster)
    preview = summarize(rules, execution, target, current, global_inflight, cluster_inflight)
    return {"revision": policy.revision, "event": event, "cluster": policy.cluster,
            "global_inflight": global_inflight, "cluster_inflight": cluster_inflight,
            "waiting_for_managed_instances": waiting,
            "parallel_safe": parallel and reservation_reason == 'reserved',
            "startup_reservation": reservation_reason, **preview}


def startup_reservations(cluster, cards, models, instances):
    """Reserve entire GPUs with unfinished instances; never guess a placement.

    Unassigned/absent/surplus instances or unsupported allocations defer all new
    starts in this cluster. Existing native scheduling continues unchanged.
    """
    budgets = [card.free_vram for card in cards]
    indexes = {(card.worker_id, card.index): i for i, card in enumerate(cards)}
    selected = {model.id: model for model in models if model.cluster_id == cluster}
    by_model = {key: [] for key in selected}
    for row in instances:
        if row.model_id in by_model:
            by_model[row.model_id].append(row)
    for key, model in selected.items():
        rows = by_model[key]
        if len(rows) != model.replicas:
            return budgets, 'instance_count_unsettled'
        for row in rows:
            allocation = row.gpu_indexes
            if (getattr(row, 'deleted_at', None) or getattr(row, 'distributed_servers', None)
                    or row.state not in ('running', 'scheduled', 'initializing', 'downloading', 'starting')
                    or type(row.worker_id) is not int or not allocation
                    or any(type(index) is not int or index < 0 for index in allocation)
                    or len(set(allocation)) != len(allocation)
                    or any((row.worker_id, index) not in indexes for index in allocation)):
                return budgets, 'placement_unsettled'
            if row.state != 'running':
                for index in allocation:
                    # Allocation telemetry may lag the scheduler. Withhold the
                    # whole card rather than counting its unused bytes twice.
                    budgets[indexes[row.worker_id, index]] = 0
    return budgets, 'reserved'
