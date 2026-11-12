"""Durable, per-deployment resource-Pending observation and contraction holds."""
from dataclasses import asdict
import hashlib
import json
import math
import time
from uuid import uuid4

from .replica_contract import private_json
from .replica_state import atomic_private
from .replica_targets import reserve_native_claims

OBSERVATION_SECONDS = 180


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, allow_nan=False).encode()).hexdigest()


def minimum(rule):
    return max(1, rule.min_replicas, rule.auto_scale_down_min_replicas)


class PendingControl:
    def __init__(self, state, *, clock=time.monotonic, wall=time.time):
        self.state, self.clock, self.wall = state, clock, wall
        self.path = state.directory/'pending-control.json'
        self.session = uuid4().hex

    def interrupted(self):
        # Holds survive an observation gap, but incomplete observation cannot
        # credit time from another process or from an unavailable snapshot.
        self.session = uuid4().hex

    def read(self):
        try:
            data = private_json(self.path)
        except FileNotFoundError:
            return {'version': 1, 'scope': None, 'models': {}}
        if (not isinstance(data, dict) or data.get('version') != 1
                or not isinstance(data.get('scope'), str) or len(data['scope']) != 64
                or not isinstance(data.get('models'), dict) or len(data['models']) > 16):
            raise ValueError('Invalid pending control state')
        for key, row in data['models'].items():
            if (not key.isdecimal() or not isinstance(row, dict)
                    or row.get('phase') not in ('observing', 'contracting', 'holding')
                    or any(type(row.get(k)) not in (int, float) or not math.isfinite(row[k])
                           or row[k] < 0 for k in ('since', 'last'))
                    or type(row.get('target')) is not int or row['target'] < 1
                    or type(row.get('expected')) is not int or row['expected'] < 0
                    or any(not isinstance(row.get(k), str) or len(row[k]) != 64 for k in ('context', 'binding'))
                    or type(row.get('revision')) is not int or row['revision'] < 1
                    or not isinstance(row.get('session'), str) or len(row['session']) != 32
                    or not isinstance(row.get('healthy_ids'), list)
                    or any(type(i) is not int or i < 1 for i in row['healthy_ids'])
                    or len(set(row['healthy_ids'])) != len(row['healthy_ids'])
                    or (row.get('request') is not None and (not isinstance(row['request'], str) or len(row['request']) != 64))):
                raise ValueError('Invalid pending deployment state')
        return data

    def observe(self, policy, devices, models, instances, native, *, pending=None):
        rules, _ = policy.effective(None)
        now = self.clock()
        if not math.isfinite(now):
            raise ValueError('Invalid observation clock')
        scope = digest([policy.cluster, policy.owner, sorted(policy.models)])
        budgets = reserve_native_claims(devices, list(instances.values()))
        inventory = {key: asdict(card) for key, card in budgets.items()}
        selected = {key: [row for row in instances.values() if row['model_id'] == key] for key in rules}
        result = {}
        with self.state.locked():
            data = self.read()
            if data['scope'] != scope:
                if pending is not None:
                    raise ValueError('Pending contraction scope changed')
                data = {'version': 1, 'scope': scope, 'models': {}}
            before = digest(data)
            for key, rule in rules.items():
                name = str(key); model = models[key]; dep = native.deployments[key]
                healthy = [ident for ident, ok in zip(dep.instance_ids, dep.healthy) if ok]
                goal = max(len(healthy), minimum(rule))
                context = digest([policy.revision, dep.binding, inventory, healthy])
                row = data['models'].get(name)
                if pending is not None:
                    result[key] = {'state': 'pending_write_read_only', 'suppress': True}
                    continue
                # A hold ignores our own Pending deletion/target events, but
                # releases on real resource/config/Ready changes or external D.
                if row and row['phase'] == 'holding':
                    if row['context'] == context and row['expected'] == dep.replicas:
                        result[key] = {'state': 'holding', 'target': dep.replicas, 'suppress': True}
                        continue
                    row = None; data['models'].pop(name, None)
                rows = selected[key]
                pending_rows = [r for r in rows if r['id'] in dep.pending_unassigned_ids]
                known_reason = bool(pending_rows) and all(
                    str(r.get('state_message') or '').startswith(('No suitable workers', 'No available workers'))
                    for r in pending_rows)
                selector = model.get('worker_selector') or {}
                slots = sum(card.free_vram // rule.vram_claim_bytes for card in budgets.values()
                            if card.kind == 'cuda' and all(dict(card.labels).get(k) == v for k, v in selector.items()))
                eligible = (len(rows) == dep.replicas == len(dep.instance_ids)
                            and model['ready_replicas'] == len(healthy)
                            and len(healthy) + len(pending_rows) == len(rows)
                            and known_reason)
                continuing = (row and row['phase'] == 'contracting' and row['context'] == context
                              and row['expected'] == dep.replicas and row['session'] == self.session
                              and 0 <= now-row['last'] <= max(15, 2*policy.reconcile.interval_s+10))
                shortage = eligible and (len(healthy) + slots < dep.replicas or continuing)
                if not shortage:
                    data['models'].pop(name, None)
                    if pending_rows or any(r['state'] != 'running' for r in rows):
                        result[key] = {'state': 'pending_not_confirmed_resource_shortage', 'suppress_down': True}
                    continue
                base = {'phase': 'observing', 'since': now, 'last': now, 'session': self.session,
                        'target': goal, 'expected': dep.replicas, 'context': context,
                        'binding': dep.binding, 'revision': policy.revision,
                        'healthy_ids': healthy, 'request': None}
                # Already at the floor/Ready target: no native action is needed.
                if goal >= dep.replicas:
                    base['phase'] = 'holding'; data['models'][name] = base
                    result[key] = {'state': 'holding_minimum', 'target': dep.replicas, 'suppress': True}
                    continue
                same_episode = (row and row['binding'] == dep.binding and row['revision'] == policy.revision
                                and row['session'] == self.session and 0 <= now-row['last'] <= max(15, 2*policy.reconcile.interval_s+10))
                if (same_episode and row['phase'] == 'contracting' and row['context'] != context):
                    same_episode = False
                if same_episode:
                    base['since'] = row['since']
                    if row['phase'] == 'contracting':
                        base['phase'] = 'contracting'
                elapsed = max(0, now-base['since'])
                if elapsed >= OBSERVATION_SECONDS:
                    base['phase'] = 'contracting'
                data['models'][name] = base
                result[key] = {'state': base['phase'], 'observed_for_s': elapsed, 'target': goal,
                               'qualified': base['phase'] == 'contracting', 'suppress': True}
            if digest(data) != before:
                atomic_private(self.path, data)
        return result

    def dispatch_locked(self, action, native):
        """Called with the final dispatch/ownership lock held."""
        data = self.read(); row = data['models'].get(str(action.model_id))
        if (not row or row['phase'] != 'contracting' or row['session'] != self.session
                or row['binding'] != action.binding or row['revision'] != action.revision
                or action.after < row['target'] or action.after != action.before-1):
            raise ValueError('Pending contraction changed')
        dep = native.deployments[action.model_id]
        healthy = [ident for ident, ok in zip(dep.instance_ids, dep.healthy) if ok]
        if healthy != row['healthy_ids']:
            raise ValueError('Protected Ready set changed')
        row['request'] = digest(asdict(action))
        atomic_private(self.path, data)

    def confirm(self, action, native):
        with self.state.locked():
            data = self.read(); row = data['models'].get(str(action.model_id))
            if not row or row.get('request') != digest(asdict(action)):
                return  # Ordinary writes have no contraction receipt.
            dep = native.deployments[action.model_id]
            if not set(row['healthy_ids']) <= set(dep.instance_ids):
                raise ValueError('Protected instance disappeared')
            row['expected'] = action.after; row['request'] = None
            if action.after <= row['target']:
                row['phase'] = 'holding'
            atomic_private(self.path, data)
