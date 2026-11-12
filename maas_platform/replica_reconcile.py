"""MaaS current-state v2 Observe loop; deliberately no model write transport."""
import asyncio
from dataclasses import asdict
import hashlib
import json
import time

from .replica_targets import plan_controlled_capacity
from .replica_resources import resolve_resources
from maas_replica_native import NativeAdapter
from .replica_apply import deployment_binding, model_settings
from .replica_state import ControlState, atomic_private


class ReconcileObserve:
    def __init__(self, normalize, pressure, *, state_directory=None,
                 clock=time.monotonic, wall_clock=time.time):
        self.normalize, self.pressure = normalize, pressure
        self.clock, self.wall_clock = clock, wall_clock
        self.state = ControlState(state_directory, clock=wall_clock) if state_directory else None
        self.signature = self.stable_since = None
        self.view = None

    async def tick(self, client, listing, policy, *, subscribed, pending_journal=None):
        self.view = None
        try:
            pending = pending_journal.read() if pending_journal is not None else None
            reads = [asyncio.create_task(listing(client, kind))
                     for kind in ('workers', 'models', 'model-instances')]
            try:
                async with asyncio.timeout(5):
                    workers, models, instances = await asyncio.gather(*reads)
            finally:
                for task in reads:
                    if not task.done():
                        task.cancel()
                await asyncio.gather(*reads, return_exceptions=True)
            read_at = self.wall_clock()
            devices = await self.normalize(policy.cluster, workers)
            if devices is None:
                raise ValueError('Incomplete or conflicting resource identity')
            selected = {key: models[key] for key in policy.models}
            runtime_policy = resolve_resources(policy, list(map(_namespace, selected.values())))
            bindings = {key: deployment_binding(row) for key, row in selected.items()}
            native = NativeAdapter(None, revision=policy.revision, cluster_id=policy.cluster,
                                   owner_id=policy.owner, model_ids=policy.models,
                                   serializer=model_settings).snapshot(
                                       list(workers.values()), list(models.values()), list(instances.values()))
            signature = hashlib.sha256(json.dumps({
                'revision': policy.revision, 'cluster': policy.cluster, 'owner': policy.owner,
                'mode': policy.execution.get('mode'),
                'devices': {key: asdict(card) for key, card in devices.items()},
                'models': {key: [bindings[key], row['replicas']] for key, row in selected.items()},
                'instances': sorted((row['id'], row['model_id'], row['state'], row.get('worker_id'),
                                     tuple(row.get('gpu_indexes') or []),
                                     json.dumps(row.get('computed_resource_claim'), sort_keys=True)) for row in instances.values()
                                    if row['model_id'] in models),
                'global_targets': sorted((row['id'], row['replicas'], row['ready_replicas'])
                                         for row in models.values()),
            }, sort_keys=True).encode()).hexdigest()
            now = self.clock()
            if signature != self.signature:
                self.signature, self.stable_since = signature, now
            plan = await asyncio.to_thread(plan_controlled_capacity, runtime_policy, devices,
                                           list(map(_namespace, models.values())),
                                           list(map(_namespace, instances.values())))
            if policy.pressure_mode == 'disabled':
                diagnostic = {'state': 'disabled', 'reasons': ['configured_disabled']}
            else:
                self.pressure.evaluate(policy.pressure, worker_ids={card.worker_id for card in devices.values()})
                diagnostic = self.pressure.diagnostic.copy()
            ownership = self.state.check(policy, selected) if self.state else 'adoption_required'
            stable = now - self.stable_since >= policy.reconcile.stability_s
            plan.update(
                state='observe_ready' if stable else 'capacity_stabilizing',
                trigger='current_state_reconcile', subscription_state='connected' if subscribed else 'polling_fallback',
                observed_at=read_at, snapshot_complete=True,
                snapshot_age_s=max(0, self.wall_clock() - read_at), interval_s=policy.reconcile.interval_s,
                stable_for_s=now - self.stable_since, eligible_gpu_count=len(devices),
                eligible_worker_ids=sorted({card.worker_id for card in devices.values()}),
                pressure_mode=policy.pressure_mode, pressure=diagnostic,
                ownership=ownership, policy_schema=2, revision=policy.revision,
                execution_state='observe' if policy.execution.get('mode') == 'observe' else 'v2_apply_not_enabled',
                pending_write=bool(pending), executable_preview_limit=0, writes_enabled=False)
            if pending:
                plan['execution_state'] = 'pending_write_read_only'
            self.view = {'devices': devices, 'models': selected, 'models_binding': bindings, 'plan': plan,
                         'runtime_policy': runtime_policy,
                         'stable': stable, 'read_monotonic': self.clock(),
                         'native_fingerprint': native.fingerprint}
        except Exception as exc:
            self.signature = self.stable_since = None
            plan = {'state': 'blocked', 'reason': getattr(exc, 'code', type(exc).__name__), 'writes_enabled': False,
                    'execution_state': 'observe_blocked', 'policy_schema': 2,
                    'snapshot_complete': False,
                    'observed_at': self.wall_clock()}
        if self.state:
            # Diagnostics failing to persist must not kill E17 observation.
            try:
                atomic_private(self.state.directory / 'status.json', plan)
            except (OSError, ValueError):
                self.signature = self.stable_since = None
                self.view = None
                plan = {'state': 'blocked', 'reason': 'control_status_unavailable',
                        'writes_enabled': False, 'execution_state': 'observe_blocked', 'policy_schema': 2}
        return plan


def _namespace(row):
    from types import SimpleNamespace
    return SimpleNamespace(**row)
