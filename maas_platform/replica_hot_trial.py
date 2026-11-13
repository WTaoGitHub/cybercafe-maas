"""Explicit live-trial helpers; never loaded by production reconciliation."""
import re
import shlex

from maas_replica_execution import WriteNotSent
from .replica_apply import model_settings


def image_alias(image, tag):
    """Change a reference while retaining its exact repository and digest."""
    if (not isinstance(image, str) or not re.fullmatch(r'[a-z0-9][a-z0-9_.-]{0,63}', tag)
            or not re.search(r'@sha256:[0-9a-f]{64}$', image)):
        raise ValueError('Trial requires a pinned image and a bounded alias')
    repository, digest = image.rsplit('@', 1)
    if repository.rfind(':') > repository.rfind('/'):
        repository = repository[:repository.rfind(':')]
    return repository+':'+tag+'@'+digest


def reordered_command(command):
    """Reorder known OCR options without inventing an unsupported argument."""
    parts = shlex.split(command)
    if (len(parts) != 4 or parts[::2] != ['--port', '--model']
            or parts[1::2] != ['{{port}}', '{{model_name}}']):
        raise ValueError('Trial command differs from the verified OCR arguments')
    return shlex.join(parts[2:]+parts[:2])


class ReplicaOnlyRequest:
    """One-deployment, bounded +/-1 recorder at the actual transport boundary."""
    def __init__(self, request, model_id, cluster_id, owner_id, record, *, cap=4):
        if (any(type(v) is not int or v < 1 for v in (model_id, cluster_id, owner_id, cap))
                or cap > 4):
            raise ValueError('Explicit bounded trial scope required')
        self.request, self.url = request, request.url
        self.model_id, self.cluster_id, self.owner_id = model_id, cluster_id, owner_id
        self.record, self.cap, self.puts = record, cap, 0

    async def __call__(self, method, path, body=None):
        if method == 'GET':
            return await self.request(method, path, body)
        if method != 'PUT' or path != f'/v2/models/{self.model_id}':
            raise WriteNotSent('trial_scope_rejected')
        row = await self.request('GET', path)
        if ((row['id'], row['cluster_id'], row['owner_principal_id']) !=
                (self.model_id, self.cluster_id, self.owner_id)):
            raise WriteNotSent('trial_identity_changed')
        target = body.get('replicas') if isinstance(body, dict) else None
        if (type(target) is not int or not 1 <= target <= self.cap
                or abs(target-row['replicas']) != 1
                or body != {**model_settings(row), 'replicas': target}):
            raise WriteNotSent('trial_non_replica_or_stale_update')
        self.puts += 1
        self.record('replica_dispatch', {'number': self.puts, 'before': row['replicas'],
                                        'after': target, 'submitted': body})
        value = await self.request(method, path, body)
        self.record('replica_readback', {'number': self.puts, 'model': value})
        return value
