"""Explicitly adopted current-state writes; serial pilot, never event replay."""
import math
import os
from pathlib import Path
import time
import json

from maas_replica_apply import LocalManagement
from maas_replica_execution import Action, Executor, PendingWrite, WriteNotSent
from maas_replica_native import NativeAdapter
from maas_replica_policy import integer
from maas_replica_pressure import PressureGate
from .replica_apply import model_settings
from .replica_state import ControlState


class ReconcileWriter:
    def __init__(self, cluster, caps, journal, state, request, *, pressure=None,
                 clock=time.monotonic, wall_clock=time.time):
        self.cluster, self.model_caps = cluster, caps
        self.journal, self.state, self.request = journal, state, request
        self.pressure = pressure if pressure is not None else PressureGate(None)
        self.clock, self.wall_clock = clock, wall_clock
        self.write_attempted = False

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
        return cls(cluster, caps, journal, state, request, pressure=PressureGate.from_environment())

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
        reason = self.authorized(policy)
        if reason:
            return reason

        async def confirm(action):
            row = await self.request('GET', f'/v2/models/{action.model_id}')
            self.state.confirm(action, row)

        adapter = NativeAdapter(self.request, revision=policy.revision, cluster_id=self.cluster,
                                owner_id=policy.owner, model_ids=policy.models,
                                grow_only=True, serializer=model_settings)
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
        generation = self.state.read()['generation']
        decision = self.pressure_decision(policy, view)
        if decision not in ('allowed', 'pressure_unknown_degraded'):
            return decision
        plan = view['plan']
        if not plan.get('up_preview_limit'):
            return 'idle'
        version = await self.request('GET', '/version')
        if version.get('version') != 'v2.2.3':
            raise ValueError('Unapproved GPUStack version')

        async def checked_request(method, path, body=None):
            if method == 'PUT':
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
                        self.write_attempted = True
                        return await self.request(method, path, body)
                except (OSError, ValueError, KeyError, TypeError):
                    if self.write_attempted:
                        raise  # Never classify an uncertain transport as unsent.
                    raise WriteNotSent('control_state_unavailable') from None
            return await self.request(method, path, body)

        adapter.request = checked_request
        snapshot = await adapter.read()
        expected = {(card.worker_id, card.index): (uuid, card.total_vram, card.allocated_vram,
                    card.reserved_vram, card.labels, card.kind) for uuid, card in view['devices'].items()}
        if snapshot.gpu_inventory.get(self.cluster, {}) != expected:
            return 'capacity_changed'
        for ident, count in plan['current'].items():
            actual = snapshot.deployments.get(ident)
            if (actual is None or actual.replicas != count
                    or actual.binding != view['models_binding'][ident] or sum(actual.healthy) < count):
                return 'deployment_changed_or_waiting'
        rules, execution = policy.effective(None)
        candidates = [key for key, count in plan['current'].items() if plan['policy_target'][key] > count]
        if not candidates:
            return 'idle'
        first = [key for key in candidates if plan['current'][key] < rules[key].min_replicas]
        ident = min(first or candidates, key=lambda key: (rules[key].priority, key))
        model = snapshot.deployments[ident]
        action = Action(snapshot.revision, snapshot.fingerprint, ident, model.cluster_id,
                        model.owner_id, model.binding, model.replicas, model.replicas + 1,
                        recovery_binding=model.recovery_binding)
        # R3a serial pilot: explicit effective limits, parallel reservation is R3b.
        executor = Executor(self.journal, adapter.read, adapter.write,
                            on_confirm=confirm, write_timeout=12, read_timeout=12)
        return await executor.submit(action, floor=0, batch_max=1,
                                     global_max=min(1, execution['max_inflight_global']),
                                     cluster_max=min(1, execution['max_inflight_per_cluster']))
