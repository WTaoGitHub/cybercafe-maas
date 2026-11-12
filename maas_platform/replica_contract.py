"""Opt-in MaaS v2 contract; the GPUStack plugin continues to accept only v1."""
from copy import deepcopy
from dataclasses import dataclass
import json
import os
import stat

from maas_replica_policy import Policy, integer


def bounded(value, low, high):
    if integer(value, low) > high:
        raise ValueError('Replica setting exceeds its bound')
    return value


def fields(raw, allowed, required=()):
    if not isinstance(raw, dict) or raw.keys() - allowed or not set(required) <= raw.keys():
        raise ValueError('Invalid replica contract fields')


@dataclass(frozen=True)
class Reconcile:
    interval_s: int = 30
    stability_s: int = 10


@dataclass(frozen=True)
class Startup:
    degraded_grace_s: int = 30
    degraded_write_interval_s: int = 60
    degraded_inflight_max: int = 1
    first_replica_fastpath: bool = False


class ControlPolicy:
    def __init__(self, raw, *, allow_apply=False):
        if not isinstance(raw, dict) or not isinstance(raw.get('models'), dict):
            raise ValueError('Expected an object policy and managed model map')
        version = integer(raw['schema_version'], 1)
        self.schema_version = version
        self.reconcile = None
        self.startup = Startup()
        base = deepcopy(raw)
        if version == 1:
            self.pressure_mode = 'required'
        elif version == 2:
            fields(raw, {'schema_version', 'revision', 'scope', 'models', 'execution',
                         'events', 'event_overrides', 'pressure', 'reconcile', 'startup'},
                   {'scope', 'models', 'execution', 'pressure', 'reconcile'})
            fields(raw['scope'], {'cluster_id', 'owner_principal_id'},
                   {'cluster_id', 'owner_principal_id'})
            fields(raw['reconcile'], {'interval_s', 'stability_s'})
            self.reconcile = Reconcile(
                bounded(raw['reconcile'].get('interval_s', 30), 1, 300),
                bounded(raw['reconcile'].get('stability_s', 10), 0, 120))
            startup = raw.get('startup', {})
            fields(startup, {'degraded_grace_s', 'degraded_write_interval_s',
                             'degraded_inflight_max', 'first_replica_fastpath'})
            fast = startup.get('first_replica_fastpath', False)
            if type(fast) is not bool:
                raise ValueError('first_replica_fastpath must be boolean')
            self.startup = Startup(
                bounded(startup.get('degraded_grace_s', 30), 0, 300),
                bounded(startup.get('degraded_write_interval_s', 60), 1, 3600),
                bounded(startup.get('degraded_inflight_max', 1), 1, 32), fast)
            pressure = deepcopy(raw['pressure'])
            fields(pressure, {'mode', 'max_sample_age_s', 'recovery_hold_s',
                              'require_worker_coverage', 'signals'}, {'mode'})
            self.pressure_mode = pressure.pop('mode')
            if self.pressure_mode not in ('required', 'advisory', 'disabled'):
                raise ValueError('Invalid pressure mode')
            if self.pressure_mode == 'required' and fast:
                raise ValueError('Required pressure cannot bypass sampling for a first replica')
            if self.pressure_mode == 'disabled':
                if pressure:
                    raise ValueError('Disabled pressure cannot contain unused thresholds')
                base.pop('pressure')
            else:
                base['pressure'] = pressure
            execution = raw['execution']
            fields(execution, {'mode', 'scale_up_batch_max', 'max_inflight_global',
                               'max_inflight_per_cluster', 'solve_timeout_s'},
                   {'mode', 'scale_up_batch_max', 'max_inflight_global', 'max_inflight_per_cluster'})
            for key in ('scale_up_batch_max', 'max_inflight_global', 'max_inflight_per_cluster'):
                bounded(execution[key], 1, 32)
            if self.startup.degraded_inflight_max > min(
                    execution['max_inflight_global'], execution['max_inflight_per_cluster']):
                raise ValueError('Degraded limit cannot exceed normal startup limits')
            base['schema_version'] = 1
            base.pop('reconcile')
            base.pop('startup', None)
        else:
            raise ValueError('Unsupported policy schema')
        self.base = Policy(base, allow_apply=allow_apply)

    def __getattr__(self, name):
        return getattr(self.base, name)


def private_json(path):
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, 'rb') as handle:
        mode = os.fstat(handle.fileno()).st_mode
        if not stat.S_ISREG(mode) or mode & 0o077:
            raise ValueError('Expected a private regular JSON file')
        raw = handle.read(65537)
    if len(raw) > 65536:
        raise ValueError('Private JSON exceeds size bound')
    return json.loads(raw)


class ControlPolicyFile:
    def __init__(self, path, *, allow_apply=False):
        self.path, self.allow_apply = path, allow_apply
        self.active = self.content = None

    def refresh(self):
        raw = private_json(self.path)
        # Canonical content preserves object identity for unchanged policy checks.
        content = json.dumps(raw, sort_keys=True, allow_nan=False)
        if content == self.content:
            return self.active
        policy = ControlPolicy(raw, allow_apply=self.allow_apply)
        if self.active is not None and policy.revision <= self.active.revision:
            raise ValueError('Policy revision must increase')
        self.active, self.content = policy, content
        return policy
