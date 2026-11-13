"""Fixed, prewarmed three-deployment startup comparison; no production defaults."""
from maas_replica_execution import WriteNotSent
from .replica_multi_trial import MultiReplicaRequest


def round_order():
    return [(repeat + 1, limit) for repeat, order in enumerate(
        ((1, 2, 4), (2, 4, 1), (4, 1, 2))) for limit in order]


def startup_policy(ids, cluster, owner, limit):
    if (len(ids) != 3 or len(set(ids)) != 3 or any(type(i) is not int or i < 1 for i in ids)
            or type(limit) is not int or limit not in (1, 2, 4)):
        raise ValueError('Three explicit deployments and limit 1/2/4 required')
    return {'schema_version': 2, 'revision': 1,
            'scope': {'cluster_id': cluster, 'owner_principal_id': owner},
            'models': {str(i): {'min_replicas': 1, 'max_replicas': 2,
                               'weight': 1, 'priority': 1} for i in ids},
            'execution': {'mode': 'observe', 'scale_up_batch_max': 1,
                          'max_inflight_global': limit, 'max_inflight_per_cluster': limit},
            'pressure': {'mode': 'disabled'},
            'reconcile': {'interval_s': 1, 'stability_s': 1},
            'startup': {'parallel_startup': limit > 1}}


def startup_counts(models, instances, cluster):
    """Conservative audit of visible unfinished targets; preserve unknown placement."""
    visible, running = {}, {}
    for row in instances:
        ident = row['model_id']
        visible[ident] = visible.get(ident, 0) + 1
        if row['state'] == 'running':
            running[ident] = running.get(ident, 0) + 1
    counts = {m['id']: max(m['replicas'], visible.get(m['id'], 0))
              - min(m['ready_replicas'], running.get(m['id'], 0)) for m in models}
    return {'global': sum(counts.values()),
            'cluster': sum(counts[m['id']] for m in models if m['cluster_id'] == cluster)}


class StartupRequest(MultiReplicaRequest):
    """The trial can only grow its three deployments by one, up to two each."""
    def __init__(self, request, caps, cluster, owner, record, floor):
        super().__init__(request, {i: 2 for i in caps}, cluster, owner, record, floor)

    async def __call__(self, method, path, body=None):
        if method == 'PUT':
            ident = next((i for i in self.caps if path == f'/v2/models/{i}'), None)
            if ident is None or not isinstance(body, dict):
                raise WriteNotSent('startup_trial_scope_rejected')
            row = await self.request('GET', path)
            if body.get('replicas') != row['replicas'] + 1:
                raise WriteNotSent('startup_trial_growth_only')
            models = (await self.request('GET', '/v2/models?perPage=100'))['items']
            instances = (await self.request('GET', '/v2/model-instances?perPage=100'))['items']
            self.record('startup_snapshot', {'model': ident, 'models': models, 'instances': instances,
                                            'inflight': startup_counts(models, instances, self.cluster)})
        return await super().__call__(method, path, body)
