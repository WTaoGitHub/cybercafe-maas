"""Explicitly adopted current-state writes with bounded startup reservations."""
import math
import os
from pathlib import Path
import time
import json

from maas_replica_apply import LocalManagement
from maas_replica_execution import Action, Executor, PendingWrite, WriteNotSent, inflight, validate_action
from maas_replica_native import NativeAdapter
from maas_replica_policy import integer
from maas_replica_pressure import PressureGate
from .replica_apply import model_settings
from .replica_state import ControlState
from .replica_native_proof import approved as native_pending_removal_approved


class ReconcileWriter:
    def __init__(self, cluster, caps, journal, state, request, *, pressure=None,
                 clock=time.monotonic, wall_clock=time.time, native_proof=None):
        self.cluster, self.model_caps = cluster, caps
        self.journal, self.state, self.request = journal, state, request
        self.pressure = pressure if pressure is not None else PressureGate(None)
        self.clock, self.wall_clock = clock, wall_clock
        self.write_attempted = False
        self.effective_startup_limit = 1
        self.execution_profile = 'serial_pilot'
        self.native_proof = native_proof

    def startup_limits(self, policy, plan, decision):
        _, execution = policy.effective(None)
        parallel = policy.startup.parallel_startup and plan.get('parallel_safe', False)
        maximum = 32 if parallel else 1
        if decision == 'pressure_unknown_degraded':
            maximum = min(maximum, policy.startup.degraded_inflight_max)
        limits = (min(maximum, execution['max_inflight_global']),
                  min(maximum, execution['max_inflight_per_cluster']))
        self.effective_startup_limit = min(limits)
        self.execution_profile = ('parallel_reserved' if parallel else 'serial_pilot')
        if decision == 'pressure_unknown_degraded':
            self.execution_profile += '_degraded'
        return limits

    @classmethod
    def from_environment(cls):
        cluster = integer(int(os.environ['MAAS_REPLICA_APPLY_CLUSTER']), 1)
        raw = json.loads(os.environ['MAAS_REPLICA_APPLY_MODEL_CAPS'])
        if not isinstance(raw, dict) or not 1 <= len(raw) <= 16:
            raise ValueError('Invalid model authorization')
        caps = {integer(int(key), 1): integer(value, 1) for key, value in raw.items()}
        if len(caps) != len(raw) or any(value > 32 for value in caps.values()):
            raise ValueError('Invalid model authorization limits')
        path = Path(os.environ['MAAS_REPLICA_WRITE_JOURNAL'])
        if not path.is_absolute():
            raise ValueError('Write journal must be absolute')
        journal = PendingWrite(path); journal.read()
        state = ControlState(os.getenv('MAAS_REPLICA_STATE_DIR', str(path.parent/'state')))
        request = LocalManagement(os.environ['MAAS_REPLICA_MANAGEMENT_URL'],
                                  os.environ['MAAS_REPLICA_MANAGEMENT_KEY_FILE'])
        return cls(cluster, caps, journal, state, request, pressure=PressureGate.from_environment(),
                   native_proof=os.getenv('MAAS_REPLICA_NATIVE_SCALE_DOWN_PROOF_FILE'))

    def authorized(self, policy):
        if policy.schema_version != 2 or policy.cluster != self.cluster:
            return 'cluster_not_authorized'
        if any(key not in self.model_caps or rule['max_replicas'] > self.model_caps[key]
               for key, rule in policy.models.items()):
            return 'deployment_scope_not_authorized'
        return None

    def pressure_decision(self, policy, view):
        if policy.pressure_mode == 'disabled':
            self.state.pressure_unknown_since(False)
            return 'allowed'
        state = self.pressure.evaluate(policy.pressure,
                                      worker_ids={card.worker_id for card in view['devices'].values()})
        if state != 'pressure_unavailable':
            self.state.pressure_unknown_since(False)
            return 'allowed' if state == 'open' else state
        if policy.pressure_mode == 'required':
            return state
        since = self.state.pressure_unknown_since(True)
        now = self.wall_clock(); owned = self.state.read()
        if not math.isfinite(now) or now < since or now < owned['last_write_at']:
            return 'control_clock_invalid'
        fast = (policy.startup.first_replica_fastpath
                and all(count == 0 for count in view['plan']['current'].values()))
        if now - since < policy.startup.degraded_grace_s and not fast:
            return 'pressure_unknown_grace'
        if owned['last_write_at'] and now - owned['last_write_at'] < policy.startup.degraded_write_interval_s:
            return 'degraded_write_cooldown'
        return 'pressure_unknown_degraded'

    async def reconcile(self, policy, observer, *, refresh):
        self.write_attempted = False
        self.effective_startup_limit = 1
        self.execution_profile = 'serial_pilot'
        reason = self.authorized(policy)
        if reason:
            return reason

        async def confirm(action):
            row = await self.request('GET', f'/v2/models/{action.model_id}')
            self.state.confirm(action, row)
            if action.after < action.before and observer.pending_control is not None:
                observer.pending_control.confirm(action, await adapter.read())

        adapter = NativeAdapter(self.request, revision=policy.revision, cluster_id=self.cluster,
                                owner_id=policy.owner, model_ids=policy.models,
                                grow_only=False, serializer=model_settings)
        if self.journal.read() is not None:
            # Read-only recovery remains possible while paused/Observe; never resubmit.
            if (await self.request('GET', '/version')).get('version') != 'v2.2.3':
                raise ValueError('Unapproved GPUStack version')
            return await Executor(self.journal, adapter.read, adapter.write,
                                  on_confirm=confirm, read_timeout=12).recover()
        if policy.execution.get('mode') != 'apply':
            return 'observe'
        view = observer.view
        if view is None or not view['stable']:
            return 'waiting_for_stable_snapshot'
        reason = self.state.check(policy, view['models'])
        if reason != 'adopted':
            return reason
        generation = self.state.rebase_targets(policy, view['models'],
                                                pending_guard=self.journal.read)['generation']
        decision = self.pressure_decision(policy, view)
        if decision not in ('allowed', 'pressure_unknown_degraded'):
            return decision
        plan = view['plan']
        global_max, cluster_max = self.startup_limits(policy, plan, decision)
        if not plan.get('up_preview_limit') and not plan.get('down_preview_limit'):
            states = {row['state'] for row in plan.get('pending_control', {}).values()}
            return ('pending_observation' if 'observing' in states else
                    'pending_hold' if states & {'holding', 'holding_minimum'} else 'idle')
        version = await self.request('GET', '/version')
        if version.get('version') != 'v2.2.3':
            raise ValueError('Unapproved GPUStack version')

        async def checked_request(method, path, body=None):
            if method == 'PUT':
                # The native API requires ModelUpdate settings. Enforce that
                # every echoed setting is from this fresh snapshot and that
                # only replicas differs; never apply stored image/env values.
                expected_row = view['models'].get(action.model_id)
                if (path != f'/v2/models/{action.model_id}' or not isinstance(body, dict)
                        or type(body.get('replicas')) is not int or body['replicas'] != action.after
                        or body != {**model_settings(expected_row), 'replicas': action.after}):
                    raise WriteNotSent('non_replica_update_rejected')
                try:
                    unchanged = refresh() is policy
                except (OSError, ValueError, KeyError, TypeError):
                    unchanged = False
                if not unchanged or policy.execution.get('mode') != 'apply':
                    raise WriteNotSent('policy_changed')
                age = self.clock() - view['read_monotonic']
                if not math.isfinite(age) or age < 0 or age > 30:
                    raise WriteNotSent('snapshot_expired')
                try:
                    latest = self.pressure_decision(policy, view)
                except (OSError, ValueError, KeyError, TypeError):
                    raise WriteNotSent('control_state_unavailable') from None
                if latest not in ('allowed', 'pressure_unknown_degraded'):
                    raise WriteNotSent(latest)
                if latest != decision:
                    raise WriteNotSent('startup_budget_changed')
                try:
                    # Serialize operator adoption/pause with the final transport
                    # boundary. An already-sent request remains subject to recovery.
                    with self.state.locked():
                        owned = self.state.read()
                        if (owned is None or owned['generation'] != generation
                                or self.state.check(policy, view['models']) != 'adopted'):
                            raise WriteNotSent('control_ownership_changed')
                        if pending_contraction:
                            if sum(snapshot.deployments[action.model_id].healthy) and not proof_for(action.model_id):
                                raise WriteNotSent('native_scale_down_proof_changed')
                            observer.pending_control.dispatch_locked(action, snapshot)
                        self.write_attempted = True
                        return await self.request(method, path, body)
                except (OSError, ValueError, KeyError, TypeError):
                    if self.write_attempted:
                        raise  # Never classify an uncertain transport as unsent.
                    raise WriteNotSent('control_state_unavailable') from None
            return await self.request(method, path, body)

        adapter.request = checked_request
        snapshot = await adapter.read()
        if snapshot.fingerprint != view['native_fingerprint']:
            return 'native_snapshot_changed'
        expected = {(card.worker_id, card.index): (uuid, card.total_vram, card.allocated_vram,
                    card.reserved_vram, card.labels, card.kind) for uuid, card in view['devices'].items()}
        if snapshot.gpu_inventory.get(self.cluster, {}) != expected:
            return 'capacity_changed'
        for ident, count in plan['current'].items():
            actual = snapshot.deployments.get(ident)
            if (actual is None or actual.replicas != count
                    or actual.binding != view['models_binding'][ident]):
                return 'deployment_changed_or_waiting'
        rules, execution = view['runtime_policy'].effective(None)
        def proof_for(ident):
            return native_pending_removal_approved(self.native_proof, getattr(self.request, 'url', ''),
                    view['models'][ident], [r for r in view['instances'].values() if r['model_id'] == ident])

        down = [key for key, count in plan['current'].items() if plan['policy_target'][key] < count]
        action = None; pending_contraction = False; missing_proof = False
        if plan.get('down_preview_limit'):
            # Prefer releasing lower-priority capacity, one native deletion at
            # a time. An unsafe model must not hide another safe correction.
            for ident in sorted(down, key=lambda key: (rules[key].priority, key), reverse=True):
                model = snapshot.deployments[ident]
                proposed = Action(snapshot.revision, snapshot.fingerprint, ident, model.cluster_id,
                                  model.owner_id, model.binding, model.replicas, model.replicas - 1,
                                  recovery_binding=model.recovery_binding)
                contraction = plan.get('pending_control', {}).get(ident, {}).get('qualified', False)
                approved = contraction and (not sum(model.healthy) or proof_for(ident))
                if contraction and not approved:
                    missing_proof = True
                    continue
                try:
                    validate_action(snapshot, proposed, floor=rules[ident].auto_scale_down_min_replicas,
                                    batch_max=1, global_max=global_max, cluster_max=cluster_max,
                                    pending_removal_proven=approved)
                except ValueError:
                    continue
                action = proposed; pending_contraction = contraction; break
        candidates = [key for key, count in plan['current'].items() if plan['policy_target'][key] > count]
        if action is None:
            if not candidates or not plan.get('up_preview_limit'):
                return ('native_scale_down_proof_required' if missing_proof else
                        'scale_down_service_protected' if down and plan.get('down_preview_limit') else 'idle')
            if (not plan.get('parallel_safe') and any(sum(snapshot.deployments[key].healthy) < count
                                                     for key, count in plan['current'].items())):
                return 'deployment_changed_or_waiting'
            first = [key for key in candidates if plan['current'][key] < rules[key].min_replicas]
            ident = min(first or candidates, key=lambda key: (rules[key].priority, key))
            model = snapshot.deployments[ident]
            total, local = inflight(snapshot.deployments, self.cluster)
            if total + 1 > global_max or local + 1 > cluster_max:
                return 'startup_budget_exhausted'
            action = Action(snapshot.revision, snapshot.fingerprint, ident, model.cluster_id,
                            model.owner_id, model.binding, model.replicas, model.replicas + 1,
                            recovery_binding=model.recovery_binding)
        # Writes remain serialized +/-1; downscale waits for native deletion
        # readback before another write, including after pause or restart.
        async def dispatch(action):
            try:
                return await adapter.write(action)
            except (ValueError, KeyError, TypeError):
                # NativeAdapter's final GET may observe another external edit.
                # A local validation rejection before the PUT is known unsent,
                # so it must not manufacture an UNKNOWN that freezes control.
                if not self.write_attempted:
                    raise WriteNotSent('deployment_changed_before_dispatch') from None
                raise

        executor = Executor(self.journal, adapter.read, dispatch,
                            on_confirm=confirm, write_timeout=12, read_timeout=12)
        return await executor.submit(action, floor=rules[action.model_id].auto_scale_down_min_replicas,
                                     batch_max=1,
                                     global_max=global_max, cluster_max=cluster_max,
                                     pending_removal_proven=pending_contraction)
