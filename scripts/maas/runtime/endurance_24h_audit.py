"""Reconcile and remove app data owned by a dedicated endurance test project."""

import json
import re
import sys

from maas_platform.artifacts import ArtifactStore, lifecycle_lock
from maas_platform.config import Settings
from maas_platform.db import Database


def reconcile(c, project):
    return c.execute('''SELECT
        (SELECT count(*) FROM tasks WHERE project=%(p)s) AS tasks,
        (SELECT count(*) FROM tasks WHERE project=%(p)s AND state='SUCCEEDED') AS succeeded,
        (SELECT count(*) FROM tasks WHERE project=%(p)s AND state IN
            ('QUEUED','RUNNING','RETRY_WAIT','CANCEL_REQUESTED')) AS open_tasks,
        (SELECT count(*) FROM task_attempts a JOIN tasks t ON a.task_id=t.id WHERE t.project=%(p)s) AS attempts,
        (SELECT count(*) FROM task_usage_events WHERE project=%(p)s) AS task_usage,
        (SELECT count(*) FROM sync_activity WHERE project=%(p)s) AS sync_calls,
        (SELECT count(*) FROM sync_activity WHERE project=%(p)s AND state='DISPATCHING') AS open_sync,
        (SELECT count(*) FROM sync_usage_events WHERE project=%(p)s) AS sync_usage,
        (SELECT count(*) FROM ocr_usage_records WHERE project=%(p)s AND source_type='task') AS task_receipts,
        (SELECT count(*) FROM ocr_usage_records WHERE project=%(p)s AND source_type='sync') AS sync_receipts,
        (SELECT count(*) FROM artifacts WHERE project=%(p)s) AS artifacts''', {'p': project}).fetchone()


def cleanup(database, store, project):
    with lifecycle_lock(store):
        with database.transaction() as c:
            before = reconcile(c, project)
            if before['open_tasks'] or before['open_sync'] or c.execute('''SELECT 1 FROM tasks
                    WHERE project=%s AND NOT starts_with(idempotency_key, %s) LIMIT 1''', (project, project + '-')).fetchone():
                raise RuntimeError('Test project has active or unrelated work')
            ids = c.execute('SELECT id FROM artifacts WHERE project=%s', (project,)).fetchall()
            for table in ('backend_activity', 'task_attempts'):
                c.execute(f'DELETE FROM {table} WHERE task_id IN (SELECT id FROM tasks WHERE project=%s)', (project,))
            for table in ('ocr_usage_records', 'task_usage_events', 'sync_usage_events', 'tasks', 'sync_activity', 'artifacts'):
                c.execute(f'DELETE FROM {table} WHERE project=%s', (project,))
            remaining = reconcile(c, project)
            if any(remaining.values()):
                raise RuntimeError('Project records remain')
        for row in ids:
            store.remove(row['id'])
        if any(store.path(row['id']).exists() for row in ids):
            raise RuntimeError('Test artifact files remain')
    return {'before': before, 'after': remaining}


if __name__ == '__main__':
    action, project = sys.argv[1:]
    if not re.fullmatch(r'test_agent_endurance_[a-z0-9]{8,24}', project):
        raise SystemExit('Dedicated endurance project required')
    config = Settings.from_env()
    database = Database(config.database_url)
    if action == 'cleanup':
        data = cleanup(database, ArtifactStore(config), project)
    elif action == 'read':
        with database.transaction() as connection:
            connection.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY')
            data = reconcile(connection, project)
    else:
        raise SystemExit('Unknown audit action')
    print(json.dumps(data))
