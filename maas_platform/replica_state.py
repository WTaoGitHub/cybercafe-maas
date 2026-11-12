"""Private durable ownership, separate from the existing pending-write journal."""
from contextlib import contextmanager
import fcntl
import hashlib
import json
import math
import os
from pathlib import Path
import stat
from tempfile import NamedTemporaryFile
import time

from maas_replica_native import binding
from .replica_apply import deployment_binding, model_settings
from .replica_contract import private_json


def atomic_private(path, data):
    path = Path(path)
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    if path.parent.is_symlink() or path.parent.stat().st_mode & 0o077:
        raise ValueError('Control state directory must be private')
    encoded = json.dumps(data, sort_keys=True, allow_nan=False)
    if len(encoded.encode()) > 65536:
        raise ValueError('Control state exceeds size bound')
    with NamedTemporaryFile(mode='w', dir=path.parent, delete=False) as handle:
        staged = Path(handle.name)
        try:
            handle.write(encoded); handle.flush(); os.fsync(handle.fileno())
            staged.replace(path)
            fd = os.open(path.parent, os.O_RDONLY)
            try:
                os.fsync(fd)
            finally:
                os.close(fd)
        finally:
            staged.unlink(missing_ok=True)


def model_identity(row):
    if (any(type(row[k]) is not int or row[k] < 1 for k in ('id', 'cluster_id', 'owner_principal_id'))
            or type(row['replicas']) is not int or row['replicas'] < 0):
        raise ValueError('Invalid model ownership identity')
    return {'target': row['replicas'], 'binding': deployment_binding(row),
            'recovery_binding': binding(row, recovery=True, serializer=model_settings)}


class ControlState:
    def __init__(self, directory, *, clock=time.time):
        self.directory = Path(directory)
        if not self.directory.is_absolute():
            raise ValueError('Control state directory must be absolute')
        self.path = self.directory / 'ownership.json'
        self.clock = clock

    @contextmanager
    def locked(self):
        self.directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        if self.directory.is_symlink() or self.directory.stat().st_mode & 0o077:
            raise ValueError('Control state directory must be private')
        fd = os.open(self.directory / 'ownership.lock', os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        try:
            if not stat.S_ISREG(os.fstat(fd).st_mode) or os.fstat(fd).st_mode & 0o077:
                raise ValueError('Control state lock is not private')
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            yield
        finally:
            os.close(fd)

    def read(self):
        if self.directory.exists() and (self.directory.is_symlink() or self.directory.stat().st_mode & 0o077):
            raise ValueError('Control state directory must be private')
        try:
            data = private_json(self.path)
        except FileNotFoundError:
            return None
        if (not isinstance(data, dict) or type(data.get('version')) is not int
                or data.get('version') != 1 or type(data.get('generation')) is not int
                or data['generation'] < 1 or type(data.get('paused')) is not bool
                or not isinstance(data.get('models'), dict) or not data['models']):
            raise ValueError('Unsupported control state')
        if any(type(data.get(k)) is not int or data[k] < 1 for k in ('cluster_id', 'owner_id')):
            raise ValueError('Invalid control scope')
        stamp = data.get('last_write_at')
        if type(stamp) not in (int, float) or not math.isfinite(stamp) or stamp < 0:
            raise ValueError('Invalid persisted write clock')
        for ident, row in data['models'].items():
            if (not isinstance(row, dict) or not ident.isascii() or not ident.isdecimal() or int(ident) < 1
                    or type(row.get('target')) is not int or row['target'] < 0
                    or any(not isinstance(row.get(k), str) or len(row[k]) != 64
                           for k in ('binding', 'recovery_binding'))):
                raise ValueError('Invalid adopted model')
        action = data.get('last_action')
        if action is not None and (not isinstance(action, str) or len(action) != 64):
            raise ValueError('Invalid confirmation receipt')
        return data

    def adopt(self, policy, models, *, expected_targets, pending_guard=None):
        if set(models) != set(policy.models) or set(expected_targets) != set(models):
            raise ValueError('Adoption must explicitly cover the complete policy scope')
        identities = {}
        for ident, row in models.items():
            if (row['id'] != ident or row['cluster_id'] != policy.cluster
                    or row['owner_principal_id'] != policy.owner
                    or type(row['replicas']) is not int or row['replicas'] < 0
                    or type(expected_targets[ident]) is not int
                    or row['replicas'] != expected_targets[ident]):
                raise ValueError('Adoption target or scope changed')
            identities[str(ident)] = model_identity(row)
        with self.locked():
            if pending_guard is not None and pending_guard():
                raise ValueError('Pending write prevents adoption')
            before = self.read()
            data = {'version': 1, 'generation': before['generation'] + 1 if before else 1,
                    'cluster_id': policy.cluster, 'owner_id': policy.owner,
                    'models': identities, 'paused': before['paused'] if before else False,
                    'last_write_at': before['last_write_at'] if before else 0,
                    'last_action': None}
            atomic_private(self.path, data)
        return data

    def check(self, policy, models):
        data = self.read()
        if data is None:
            return 'adoption_required'
        if data['paused']:
            return 'controller_paused'
        if ((data['cluster_id'], data['owner_id']) != (policy.cluster, policy.owner)
                or set(data['models']) != {str(k) for k in policy.models}):
            return 'adoption_scope_changed'
        for ident in policy.models:
            if ident not in models or models[ident]['id'] != ident:
                return 'adoption_scope_changed'
            if (models[ident]['cluster_id'], models[ident]['owner_principal_id']) != (policy.cluster, policy.owner):
                return 'adoption_scope_changed'
            # The controller owns replica counts only. Configuration is adopted
            # as current input, not a desired value to restore or a pause signal.
            model_identity(models[ident])
        return 'adopted'

    def rebase_targets(self, policy, models, *, pending_guard):
        """Accept observed counts as the next action's before-value, never its goal."""
        with self.locked():
            if pending_guard() is not None:
                raise ValueError('Pending write prevents baseline refresh')
            if self.check(policy, models) != 'adopted':
                raise ValueError('Control ownership changed')
            data = self.read()
            identities = {str(key): model_identity(row) for key, row in models.items()}
            changed = {str(key): {'before': data['models'][str(key)]['target'],
                                 'observed': row['replicas']}
                       for key, row in models.items()
                       if data['models'][str(key)]['target'] != row['replicas']}
            configurations = [key for key, row in identities.items()
                              if data['models'][key]['recovery_binding'] != row['recovery_binding']]
            if changed or any(data['models'][key] != identity for key, identity in identities.items()):
                now = self.clock()
                if type(now) not in (int, float) or not math.isfinite(now) or now < data['last_write_at']:
                    raise ValueError('Control clock moved backwards')
                data['generation'] += 1
                data['models'] = identities
                if changed:
                    data['last_external_change'] = {'observed_at': now, 'models': changed}
                if configurations:
                    data['last_configuration_change'] = {'observed_at': now, 'model_ids': sorted(map(int, configurations))}
                # A future, identical 0->1 action must not reuse an older
                # confirmation receipt after an external reset back to zero.
                data['last_action'] = None
                atomic_private(self.path, data)
            return data

    def pause(self):
        return self.set_paused(True)

    def set_paused(self, paused, *, pending_guard=None):
        """Shares the final dispatch lock; never acknowledges an in-flight pause."""
        if type(paused) is not bool:
            raise ValueError('Pause must be boolean')
        with self.locked():
            data = self.read()
            if data is None:
                raise ValueError('No adopted scope')
            if not paused and pending_guard is not None and pending_guard():
                raise ValueError('Pending write prevents resume')
            if data['paused'] != paused:
                data['generation'] += 1; data['paused'] = paused
                atomic_private(self.path, data)
            return data

    def pressure_unknown_since(self, active):
        with self.locked():
            data = self.read()
            if data is None:
                raise ValueError('No adopted scope')
            previous = data.get('pressure_unknown_since')
            if previous is not None and (type(previous) not in (int, float)
                                         or not math.isfinite(previous) or previous < 0):
                raise ValueError('Invalid pressure recovery clock')
            now = self.clock()
            if (type(now) not in (int, float) or not math.isfinite(now) or now < data['last_write_at']
                    or (previous is not None and now < previous)):
                raise ValueError('Control clock moved backwards')
            value = (previous if previous is not None else now) if active else None
            if value != previous:
                data['pressure_unknown_since'] = value
                atomic_private(self.path, data)
            return value

    def confirm(self, action, row):
        """Idempotent confirmation hook; call before clearing PendingWrite."""
        action_id = hashlib.sha256(json.dumps(vars(action), sort_keys=True).encode()).hexdigest()
        identity = model_identity(row)
        if (row['id'] != action.model_id or row['replicas'] != action.after
                or row['cluster_id'] != action.cluster_id or row['owner_principal_id'] != action.owner_id
                or (identity['binding'] != action.binding
                    and identity['recovery_binding'] != action.recovery_binding)):
            raise ValueError('Unconfirmed action cannot update ownership')
        with self.locked():
            data = self.read()
            if data is None or data['cluster_id'] != action.cluster_id or data['owner_id'] != action.owner_id:
                raise ValueError('Confirmation scope differs')
            if data['last_action'] == action_id:
                if data['models'].get(str(action.model_id)) != identity:
                    raise ValueError('Confirmed receipt changed')
                return
            before = data['models'].get(str(action.model_id))
            if before is None or before['target'] != action.before or before['binding'] != action.binding:
                raise ValueError('Adopted baseline changed before confirmation')
            now = self.clock()
            if type(now) not in (int, float) or not math.isfinite(now) or now < data['last_write_at']:
                raise ValueError('Control write clock moved backwards')
            data['models'][str(action.model_id)] = identity
            data['last_write_at'] = now; data['last_action'] = action_id
            atomic_private(self.path, data)
