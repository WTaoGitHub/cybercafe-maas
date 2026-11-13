"""Explicit multi-deployment live-trial transport and frozen small-case oracle."""
import time

from maas_replica_execution import WriteNotSent
from .replica_apply import model_settings


def expectations(free_bytes, claim):
    if (len(free_bytes) != 2 or any(type(v) is not int or v < 0 for v in free_bytes)
            or type(claim) is not int or claim <= 0):
        raise ValueError('Two verified card budgets required')
    slots = [v//claim for v in free_bytes]
    if slots != [3, 3]:
        raise ValueError('Live inventory differs from the frozen six-slot test plan')
    return {'slots_per_card': slots, 'equal': [2, 2, 2],
            'weighted_allowed': [[3, 2, 1], [3, 1, 2]],
            'priority_before': [1, 1, 0], 'priority_after': [1, 1, 1],
            'pending_before': [3, 3, 3], 'pending_after': [2, 2, 2],
            'minimum_targets': [2, 2, 2], 'minimum_ready': [1, 2, 2]}


class MultiReplicaRequest:
    def __init__(self, request, caps, cluster, owner, record, floor):
        if (len(caps) != 3 or any(type(k) is not int or k < 1 or type(v) is not int
                                or not 1 <= v <= 3 for k, v in caps.items())):
            raise ValueError('Explicit three-deployment trial caps required')
        self.request, self.url, self.caps = request, request.url, caps
        self.cluster, self.owner, self.record, self.floor = cluster, owner, record, floor

    async def __call__(self, method, path, body=None):
        if method == 'GET':
            return await self.request(method, path, body)
        ident = next((k for k in self.caps if path == f'/v2/models/{k}'), None)
        if method != 'PUT' or ident is None:
            raise WriteNotSent('multi_trial_scope_rejected')
        row = await self.request('GET', path)
        target = body.get('replicas') if isinstance(body, dict) else None
        if ((row['id'], row['cluster_id'], row['owner_principal_id']) != (ident, self.cluster, self.owner)
                or type(target) is not int or not 1 <= target <= self.caps[ident]
                or abs(target-row['replicas']) != 1
                or (target < row['replicas'] and target < self.floor(ident))
                or body != {**model_settings(row), 'replicas': target}):
            raise WriteNotSent('multi_trial_non_replica_or_stale_update')
        rows = (await self.request('GET', '/v2/models?perPage=100'))['items']
        counts = {r['id']: r['replicas'] for r in rows if r['id'] in self.caps}
        if counts.get(ident) != row['replicas'] or counts.keys() != self.caps.keys():
            raise WriteNotSent('multi_trial_target_changed')
        self.record('dispatch', {'model': ident, 'before': row['replicas'], 'after': target,
                    'targets_before': counts, 'monotonic': time.monotonic(), 'submitted': body})
        result = await self.request(method, path, body)
        self.record('readback', {'model': ident, 'row': result})
        return result
