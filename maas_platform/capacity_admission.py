"""Shared PostgreSQL admission accounting, called under the maintenance row lock."""
from collections import Counter
import json

from .capacity import Policy, catalog_routes, policy_hash, limits
from .config import private_text
from .errors import ServiceError
from .quarantine import blocks, unresolved


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


def report_unknown(c, budget):
    if budget.get('source') == 'gpustack_ready':
        count = c.execute("""SELECT (SELECT count(*) FROM sync_activity WHERE state='UNKNOWN')
                                  + (SELECT count(*) FROM backend_activity WHERE state='UNKNOWN') AS n""").fetchone()['n']
        budget['unknown_capacity'] = {'unresolved': count, 'record_only': count, 'reconciliation_only': count,
                                      'charged_as_occupancy': 0, 'covered_by_gpu_exclusion': 0}
    else:
        isolate_budget(budget, unresolved(c))


def isolate_budget(budget, unknown):
    """Remove quarantined hardware from legacy topology budgets."""
    rules = budget.get('isolation_policy')
    debts = [r for r in unknown if rules and r.get('isolated_gpu_uuid')]
    budget['unknown_capacity'] = {'unresolved': len(unknown), 'covered_by_gpu_exclusion': len(debts),
                                  'charged_as_occupancy': len(unknown) - len(debts)}
    cards = {r['isolated_gpu_uuid'] for r in debts}
    if not cards:
        return set()
    for key, pool in budget['pools'].items():
        rule = rules['pools'][key]
        # Only these routes fence the destination on every GPU start, including late retries.
        overlap = 'processes' in pool and (
            'route_gpus' not in pool or not cards.isdisjoint(pool['route_gpus']))
        guarded = ('processes' in pool and not overlap) or 'replica_routes' in pool
        ready = {i: gpu for i, gpu in pool['ready_gpus'].items() if guarded and gpu not in cards}
        pool['ready_gpus'] = ready
        pool['ready_ids'] = [i for i in pool['ready_ids'] if str(i) in ready]
        for field in ('processes', 'replica_routes'):
            if field in pool:
                pool[field] = {name: gpu for name, gpu in pool[field].items() if guarded and gpu not in cards}
        total = min(pool['total'], len(ready) * rule['slots'])
        if rule['limit'] is not None:
            debt = sum(r.get('dispatch_pool_id') == pool['model_id'] for r in debts)
            total = min(total, max(0, rule['limit'] - debt))
        pool.update(limits(total, rule['sync_percent']))
        if not total:
            pool['reason'] = 'gateway_reaches_isolated_gpu' if overlap else 'execution_quarantine'
    total = sum(p['total'] for p in budget['pools'].values())
    if rules['global_limit'] is not None:
        total = min(total, max(0, rules['global_limit'] - len(debts)))
    budget['global'].update(limits(min(total, budget['global']['total']), rules['sync_percent']))
    budget['isolated_gpus'] = sorted(cards)
    return cards


def queue_counts(c, project, names):
    # An attempt that could return to the queue keeps its original place reserved.
    return c.execute('''SELECT count(*) AS total, count(*) FILTER (WHERE t.project=%s) AS own,
        count(*) FILTER (WHERE t.model=ANY(%s)) AS pool
        FROM tasks t LEFT JOIN backend_activity b ON b.token=t.token
        WHERE t.deadline>clock_timestamp() AND (t.state IN ('QUEUED','RETRY_WAIT')
          OR (t.state='RUNNING' AND (b.token IS NULL OR t.max_attempts>1)))''', (project, names)).fetchone()


class Admission:
    def __init__(self, c, settings, revision, *, include_preparing, unknown=None):
        policy, self.aliases, local_revision = configuration(settings)
        if revision != local_revision:
            raise unavailable()
        self.pool_ids = {name: policy.pools[key].model_id for name, key in self.aliases.items()}
        self.pools = policy.pools
        self.automatic = policy.global_limit is None
        self.gpustack_routed = policy.uses_ready_replicas
        self.revision = revision
        self.budget = read_budget(c, revision)
        if self.budget and (self.budget.get('source') == 'gpustack_ready') != self.gpustack_routed:
            raise unavailable()
        self.unknown = ()
        self.project_limit = 0 if self.automatic else settings.max_project_running
        self.claimed = Counter()
        self.asynchronous = Counter()
        self.project = Counter()
        self.gpu = Counter()
        if not self.budget:
            return
        if self.gpustack_routed:
            rows = c.execute('''SELECT model,project,count(*) AS n,asynchronous FROM maas_dispatch_slots
                                WHERE %s OR dispatched GROUP BY model,project,asynchronous''',
                             (include_preparing,)).fetchall()
        else:
            self.unknown = unresolved(c) if unknown is None else unknown
            excluded = isolate_budget(self.budget, self.unknown)
            if not self.automatic:
                self.project.update(r['project'] for r in self.unknown if r.get('isolated_gpu_uuid') in excluded)
            rows = c.execute('''SELECT model,project,count(*) AS n,asynchronous,dispatch_gpu_uuid,dispatch_pool_id
                                FROM inference_slots
                                WHERE NOT backend_finished AND (%s OR dispatched)
                                  AND (isolated_gpu_uuid IS NULL OR NOT (isolated_gpu_uuid=ANY(%s::text[])))
                                GROUP BY model,project,asynchronous,dispatch_gpu_uuid,dispatch_pool_id''',
                             (include_preparing, list(excluded))).fetchall()
        if self.automatic:
            self.project_limit = self.budget['global']['total']
        for row in rows:
            key, n = self.aliases.get(row['model']), row['n']
            self.project[row['project']] += n
            self.claimed[key] += n
            if row.get('dispatch_gpu_uuid'):
                self.gpu[row['dispatch_pool_id'], row['dispatch_gpu_uuid']] += n
            if row['asynchronous']:
                self.asynchronous[key] += n
        self.total, self.async_total = self.claimed.total(), self.asynchronous.total()

    def pool(self, model):
        return self.budget['pools'].get(self.aliases.get(model), {}) if self.budget else {}

    def fresh(self, c):
        return bool(self.budget and c.execute('''SELECT 1 FROM capacity_snapshot WHERE id=1
            AND policy_hash=%s AND expires_at>clock_timestamp()''', (self.revision,)).fetchone())

    def dispatch(self, model, route, unknown=None):
        """Pick a fresh, admissible destination. The caller persists the pair before sending."""
        if self.gpustack_routed:
            return route, None, None
        if unknown is None:
            unknown = self.unknown
        pool = self.pools.get(self.aliases.get(model))
        pool_id = self.pool_ids.get(model)
        if pool and pool.guard_backend_version:
            processes = self.pool(model).get('processes', {})
            other = [r for r in unknown if not r.get('isolated_gpu_uuid')]
            if blocks(other, model, route, pool_id):
                return None, None, 'BACKEND_QUARANTINED'
            if not processes:
                return None, None, ('BACKEND_QUARANTINED' if self.pool(model).get('reason') in
                                    ('execution_quarantine', 'gateway_reaches_isolated_gpu') else 'CAPACITY_REDUCED')
            return route, None, None
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
