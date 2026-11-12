"""v2 authoritative targets; legacy plugin/event planning remains additive."""
from maas_replica_planner import plan_capacity
from maas_replica_policy import solve, summarize


def plan_controlled_capacity(policy, devices, models, instances):
    plan = plan_capacity(policy, devices, models, instances,
                         parallel=policy.startup.parallel_startup)
    rules, execution = policy.effective(None)
    cards = sorted(devices.values(), key=lambda card: (card.worker_id, card.index))
    indexes = {(card.worker_id, card.index): i for i, card in enumerate(cards)}
    selected = {model.id: model for model in models if model.id in rules}
    eligible = {key: [i for i, card in enumerate(cards) if card.kind == 'cuda'
                     and all(dict(card.labels).get(k) == v
                             for k, v in (model.worker_selector or {}).items())]
                for key, model in selected.items()}
    reclaimed = [0]*len(cards)
    proof = 'managed_claims_reclaimed'
    counts = {key: 0 for key in rules}
    for row in instances:
        if row.model_id not in rules:
            continue  # Unmanaged allocations remain in native allocated_vram.
        counts[row.model_id] += 1
        allocation = row.gpu_indexes or []
        distributed = getattr(row, 'distributed_servers', None)
        if (getattr(row, 'deleted_at', None) or row.state not in (
                'running', 'scheduled', 'initializing', 'downloading', 'starting', 'pending')
                or (distributed is not None and (not isinstance(distributed, dict)
                    or bool(distributed.keys() - {'mode', 'download_model_files', 'subordinate_workers'})
                    or distributed.get('subordinate_workers') not in (None, [])))):
            proof = 'allocation_unsettled'; break
        if not allocation and row.worker_id is None and row.state == 'pending':
            continue  # No placement to reclaim; it is not additional free VRAM.
        if len(allocation) != 1 or type(row.worker_id) is not int or type(allocation[0]) is not int:
            proof = 'allocation_unsettled'; break
        at = indexes.get((row.worker_id, allocation[0]))
        if at is None:
            continue  # An absent/non-ready card contributes no usable capacity.
        claim = rules[row.model_id].vram_claim_bytes
        resource = getattr(row, 'computed_resource_claim', None)
        if resource is not None and (not isinstance(resource, dict)
                or resource.get('vram') not in ({str(allocation[0]): claim}, {allocation[0]: claim})):
            proof = 'allocation_unsettled'; break
        reclaimed[at] += claim
    if any(counts[key] > selected[key].replicas for key in rules):
        proof = 'allocation_unsettled'
    if proof == 'managed_claims_reclaimed':
        # Only count bytes that native telemetry actually reports as allocated.
        # Reclaiming is a planning proof, never an allocation or placement write.
        budgets = [min(max(0, card.total_vram - card.reserved_vram),
                       card.free_vram + min(card.allocated_vram, reclaimed[i]))
                   for i, card in enumerate(cards)]
        capacity = solve(rules, budgets, eligible, plan['current'],
                         execution.get('solve_timeout_s', 1))
    else:
        # Unknown placement cannot justify resource-driven shrink. An explicit
        # policy cap can still correct external over-scaling, subject to health.
        capacity = {key: min(count, rules[key].max_replicas)
                    for key, count in plan['current'].items()}
    targets = {}
    for key, count in plan['current'].items():
        desired = min(rules[key].max_replicas, capacity[key])
        # New starts retain the existing additive/whole-pending-card proof.
        # A hypothetical repacking of current replicas never authorizes starts.
        if desired > count:
            desired = min(desired, plan['policy_target'][key])
        targets[key] = desired
    preview = summarize(rules, execution, targets, plan['current'],
                        plan['global_inflight'], plan['cluster_inflight'])
    return {**plan, **preview, 'capacity_target': capacity,
            'downscale_capacity_proof': proof, 'target_authority': 'policy_and_current_capacity'}
