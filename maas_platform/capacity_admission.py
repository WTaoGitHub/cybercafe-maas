"""Shared PostgreSQL admission accounting, called under the maintenance row lock."""
from collections import Counter
import json

from .capacity import Policy, catalog_routes, policy_hash
from .config import private_text
from .errors import ServiceError


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
        if policy.global_limit > settings.max_running or any(
                model.get('upstream_model') and name not in aliases for name, model in catalog.items()):
            raise ValueError('Incomplete capacity configuration')
    except (OSError, ValueError, TypeError, AttributeError):
        raise unavailable() from None
    return policy, aliases, policy_hash(policy, names, settings.gpustack_url.rstrip('/') + '/v2/')


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
        self.revision = revision
        self.budget = read_budget(c, revision)
        self.project_limit = settings.max_project_running
        self.claimed = Counter()
        self.asynchronous = Counter()
        self.project = Counter()
        if not self.budget:
            return
        rows = c.execute('''SELECT t.model,t.project,count(*) AS n,true AS async
              FROM tasks t WHERE t.state IN ('RUNNING','CANCEL_REQUESTED') AND
                (%s OR EXISTS(SELECT 1 FROM backend_activity b WHERE b.token=t.token))
              GROUP BY t.model,t.project
              UNION ALL SELECT model,project,count(*),false FROM sync_activity
              WHERE state='DISPATCHING' GROUP BY model,project''', (include_preparing,)).fetchall()
        for row in rows:
            key, n = self.aliases.get(row['model']), row['n']
            self.project[row['project']] += n
            self.claimed[key] += n
            if row['async']:
                self.asynchronous[key] += n
        self.total, self.async_total = self.claimed.total(), self.asynchronous.total()

    def pool(self, model):
        return self.budget['pools'].get(self.aliases.get(model), {}) if self.budget else {}

    def fresh(self, c):
        return bool(self.budget and c.execute('''SELECT 1 FROM capacity_snapshot WHERE id=1
            AND policy_hash=%s AND expires_at>clock_timestamp()''', (self.revision,)).fetchone())

    def allows(self, model, project, *, asynchronous):
        pool = self.pool(model)
        if not pool.get('total'):
            return False
        key, total = self.aliases[model], self.budget['global']
        return (self.project[project] < self.project_limit and self.total < total['total']
                and self.claimed[key] < pool['total']
                and (not asynchronous or (self.async_total < total['async_limit']
                                            and self.asynchronous[key] < pool['async_limit'])))
