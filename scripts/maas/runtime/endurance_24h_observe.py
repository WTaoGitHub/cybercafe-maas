"""Emit only budget and activity aggregates from the existing control-plane container."""

import json
from pathlib import Path
import re
import shutil
import sys
import time

import psycopg
from psycopg.rows import dict_row

from maas_platform.config import Settings


MODELS = ('ocr.default', 'ocr.ovisocr3', 'document.unlimited-ocr-2')


def read_sample(connection, settings, project, storage):
    with connection.transaction():
        connection.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY')
        row = connection.execute('''SELECT m.generation, m.paused, c.budget,
            c.expires_at > clock_timestamp() AND m.capacity_policy_hash = c.policy_hash AS fresh
            FROM maintenance m JOIN capacity_snapshot c ON m.id=c.id WHERE m.id=1''').fetchone()
        groups = connection.execute('''SELECT model, count(*) AS total, count(*) FILTER (WHERE project=%s) AS own
                FROM maas_dispatch_slots WHERE dispatched GROUP BY model''', (project,)).fetchall()
        counters = connection.execute('''SELECT
            (SELECT count(*) FROM sync_activity WHERE state='UNKNOWN') AS unknown_sync,
            (SELECT count(*) FROM backend_activity WHERE state='UNKNOWN') AS unknown_async,
            (SELECT coalesce(extract(epoch FROM now()-min(created_at)),0) FROM tasks
                WHERE state IN ('QUEUED','RETRY_WAIT')) AS oldest''').fetchone()
        if storage is None or time.monotonic() - storage['mono'] >= 15:
            storage = connection.execute('''SELECT coalesce(sum(size), 0)::bigint AS bytes,
                coalesce(sum(size) FILTER (WHERE project=%s), 0)::bigint AS project_bytes FROM artifacts
                WHERE deleted_at IS NULL''', (project,)).fetchone()
            storage['mono'] = time.monotonic()
    budget = row['budget']
    by_model = {x['model']: x for x in groups}
    disk = shutil.disk_usage(settings.artifact_root)
    memory = int(next(line.split()[1] for line in Path('/proc/meminfo').read_text().splitlines()
                      if line.startswith('MemAvailable:'))) * 1024
    cpu = [int(value) for value in Path('/proc/stat').read_text().splitlines()[0].split()[1:9]]
    return {
        'mono': time.monotonic(), 'fresh': bool(row['fresh']) and budget['source'] == 'gpustack_ready',
        'paused': row['paused'], 'maintenance': row['generation'], 'capacity': budget['global']['total'],
        'budget': [budget['pools'][name]['total'] for name in MODELS] + [budget['global']['async_limit']],
        'unknown': [counters['unknown_sync'], counters['unknown_async']], 'oldest_queue': float(counters['oldest']),
        'used': sum(g['total'] for g in groups), 'own': sum(g['own'] for g in groups),
        'by_pool': {name: by_model.get(name, {}).get('total', 0) for name in MODELS},
        'own_by_pool': {name: by_model.get(name, {}).get('own', 0) for name in MODELS},
        'bytes': storage['bytes'], 'project_bytes': storage['project_bytes'],
        'quota': settings.max_storage_bytes, 'project_quota': settings.max_project_storage_bytes,
        'disk_fraction': disk.free / disk.total, 'disk_reserve_bytes': max(0, disk.free - disk.total // 5),
        'memory_bytes': memory,
    }, storage, (sum(cpu), cpu[3] + cpu[4])


def observe(project, once=False):
    if not re.fullmatch(r'test_agent_endurance_[a-z0-9]{8,24}', project):
        raise SystemExit('Invalid test project')
    settings = Settings.from_env()
    storage = previous = None
    deadline = time.monotonic()
    with psycopg.connect(settings.database_url, row_factory=dict_row, autocommit=True, connect_timeout=5,
                         options='-c default_transaction_read_only=on -c statement_timeout=800') as connection:
        while True:
            sample, storage, cpu = read_sample(connection, settings, project, storage)
            sample['cpu'] = (1 - (cpu[1] - previous[1]) / (cpu[0] - previous[0])
                             if previous and cpu[0] > previous[0] else 0.0)
            previous = cpu
            print(json.dumps(sample), flush=True)
            if once:
                break
            deadline += 1
            time.sleep(max(0, deadline - time.monotonic()))


if __name__ == '__main__':
    observe(sys.argv[1], '--once' in sys.argv[2:])
