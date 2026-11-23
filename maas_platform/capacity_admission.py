"""Shared PostgreSQL admission accounting, called under the maintenance row lock."""
from collections import Counter
import json

from .capacity import Policy, catalog_routes, policy_hash
from .config import private_text
from .errors import ServiceError
from .quarantine import blocks


def unavailable():
    return ServiceError('CAPACITY_UNAVAILABLE', 'Model execution capacity is unavailable', 503, True)


def configuration(settings):
    if not settings.capacity_policy_file:
        raise unavailable()
    try:
        policy = Policy.model_validate_json(private_text(settings.capacity_policy_file))
        catalog = json.loads(settings.catalog_file.read_text())
        names = catalog_routes(policy, catalog)
        aliases = {alias: key for key, pool in policy.pools.items() for alias in pool.routes}
        explicit_limit = policy.global_limit
        if ((explicit_limit is not None and explicit_limit > settings.max_running)
                or any(model.get('upstream_model') and name not in aliases for name, model in catalog.items())):
            raise ValueError('Incomplete capacity configuration')
    except (OSError, ValueError, TypeError, AttributeError):
        raise unavailable() from None
    return policy, aliases, policy_hash(policy, names, settings.gpustack_url.rstrip('/') + '/v2/')


def follows_ready_capacity(settings):
    try:
        policy, _, _ = configuration(settings)
    except ServiceError:
        return False
    return policy.global_limit is None


def read_budget(c, revision):
    row = c.execute('''SELECT budget FROM capacity_snapshot WHERE id=1 AND policy_hash=%s
                       AND expires_at>clock_timestamp()''', (revision,)).fetchone()
    return row['budget'] if row else None


def queue_counts(c, project, names):
    # An attempt that could return to the queue keeps its original place reserved.
    return c.execute('''SELECT count(*) AS total, count(*) FILTER (WHERE t.project=%s) AS own,
        count(*) FILTER (WHERE t.model=ANY(%s)) AS pool
        FROM tasks t LEFT JOIN backend_activity b ON b.token=t.token
        WHERE t.deadline>clock_timestamp() AND (t.state IN ('QUEUED','RETRY_WAIT')
          OR (t.state='RUNNING' AND (b.token IS NULL OR t.max_attempts>1)))''', (project, names)).fetchone()


class Admission:
    def __init__(self, c, settings, revision, *, include_preparing):
        policy, self.aliases, local_revision = configuration(settings)
        if revision != local_revision:
            raise unavailable()
        self.pool_ids = {name: policy.pools[key].model_id for name, key in self.aliases.items()}
        self.pools = policy.pools
        self.automatic = policy.global_limit is None
        self.revision = revision
        self.budget = read_budget(c, revision)
        self.project_limit = settings.max_project_running
        self.claimed = Counter()
        self.asynchronous = Counter()
        self.project = Counter()
        self.gpu = Counter()
        if not self.budget:
            return
        if self.automatic:
            self.project_limit = self.budget['global']['total']
        rows = c.execute('''SELECT model,project,count(*) AS n,asynchronous,dispatch_gpu_uuid,dispatch_pool_id
                            FROM inference_slots
                            WHERE NOT backend_finished AND (%s OR dispatched)
                            GROUP BY model,project,asynchronous,dispatch_gpu_uuid,dispatch_pool_id''',
                         (include_preparing,)).fetchall()
        for row in rows:
            key, n = self.aliases.get(row['model']), row['n']
            self.project[row['project']] += n
            self.claimed[key] += n
            if row['dispatch_gpu_uuid']:
                self.gpu[row['dispatch_pool_id'], row['dispatch_gpu_uuid']] += n
            if row['asynchronous']:
                self.asynchronous[key] += n
        self.total, self.async_total = self.claimed.total(), self.asynchronous.total()

    def pool(self, model):
        return self.budget['pools'].get(self.aliases.get(model), {}) if self.budget else {}

    def fresh(self, c):
        return bool(self.budget and c.execute('''SELECT 1 FROM capacity_snapshot WHERE id=1
            AND policy_hash=%s AND expires_at>clock_timestamp()''', (self.revision,)).fetchone())

    def dispatch(self, model, route, unknown):
        """Pick a fresh, admissible destination. The caller persists the pair before sending."""
        pool = self.pools.get(self.aliases.get(model))
        pool_id = self.pool_ids.get(model)
        if not pool or not pool.replica_routes:
            return (None, None, 'BACKEND_QUARANTINED') if blocks(unknown, model, route, pool_id) else (route, None, None)
        observed = self.pool(model).get('replica_routes', {})
        candidates = [(name, pin.gpu_uuid) for name, pin in pool.replica_routes.items()
                      if observed.get(name) == pin.gpu_uuid]
        free = [(self.gpu[pool_id, gpu], name, gpu) for name, gpu in candidates
                if not blocks(unknown, model, name, pool_id, gpu)]
        if free:
            load, name, gpu = min(free)
            if load < pool.slots_per_replica:
                return name, gpu, None
        return None, None, 'BACKEND_QUARANTINED' if candidates and not free else 'CAPACITY_REDUCED'

    def allows(self, model, project, *, asynchronous):
        pool = self.pool(model)
        if not pool.get('total'):
            return False
        key, total = self.aliases[model], self.budget['global']
        return (self.project[project] < self.project_limit and self.total < total['total']
                and self.claimed[key] < pool['total']
                and (not asynchronous or (self.async_total < total['async_limit']
                                            and self.asynchronous[key] < pool['async_limit'])))
