"""Measure a PP withdrawal in a test database using the GPU-start transaction clock.

Set MAAS_DATABASE_URL in the test control environment. Use a dedicated test_agent_pp_*
project for continuous traffic. If the fault uses an existing dedicated route
credential, pass its project as --origin-project to read. Install before sending traffic:

  python -m scripts.maas.runtime.pp_grant_window install PROJECT
  python -m scripts.maas.runtime.pp_grant_window traffic PROJECT --url http://127.0.0.1:18830 --seconds 300
  python -m scripts.maas.runtime.pp_grant_window read PROJECT --kind sync --token TOKEN
  python -m scripts.maas.runtime.pp_grant_window remove PROJECT

Read during UNKNOWN or after signed termination. Remove only after exporting all
samples. Run the traffic command in the test API environment with the project's
binding and model access. The caller owns faults and cleanup of the project itself.
"""
import argparse
import json
import os
import re
import time
from threading import Event
from pathlib import Path

import psycopg
from psycopg.rows import dict_row


TABLES = {'sync': 'sync_activity', 'async': 'backend_activity'}


def project_name(value):
    if not re.fullmatch(r'test_agent_pp_[a-z0-9_]{1,45}', value):
        raise ValueError('Use a dedicated test_agent_pp_ project')
    return value


def install(conn, project):
    schema = project_name(project)
    conn.execute(f'CREATE SCHEMA {schema}')
    conn.execute(f'''CREATE TABLE {schema}.grants (
        token text PRIMARY KEY, kind text NOT NULL, gpu text NOT NULL, process text,
        admitted timestamptz NOT NULL, granted timestamptz NOT NULL DEFAULT clock_timestamp())''')
    conn.execute(f'''CREATE FUNCTION {schema}.track() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            IF TG_TABLE_NAME='sync_activity' THEN
                IF NEW.project <> '{schema}' THEN RETURN NULL; END IF;
            ELSIF NOT EXISTS (SELECT 1 FROM public.tasks WHERE id=NEW.task_id AND project='{schema}') THEN
                RETURN NULL;
            END IF;
            INSERT INTO {schema}.grants (token,kind,gpu,process,admitted)
                VALUES (NEW.token,TG_TABLE_NAME,NEW.gpu_uuid,NEW.gpu_instance,NEW.created_at);
            RETURN NULL;
        END; $$''')
    for table in TABLES.values():
        conn.execute(f'''CREATE TRIGGER {schema} AFTER UPDATE OF gpu_uuid ON public.{table}
            FOR EACH ROW WHEN (OLD.gpu_uuid IS NULL AND NEW.gpu_uuid IS NOT NULL)
            EXECUTE FUNCTION {schema}.track()''')


def read_window(conn, project, kind, token, origin_project=None):
    schema = project_name(project)
    source = ('sync_activity b' if kind == 'sync' else
              'backend_activity b JOIN tasks t ON t.id=b.task_id')
    row = conn.execute(f"""SELECT b.state,b.unknown_since,b.updated_at,b.gpu_uuid,b.resolution_reason,
        clock_timestamp() AS sampled FROM {source} WHERE b.token=%s AND project=%s
        AND b.gpu_uuid IS NOT NULL AND b.unknown_since IS NOT NULL""", (token, origin_project or project)).fetchone()
    if not row:
        raise ValueError('No matching project fault with an UNKNOWN timestamp and GPU grant')
    if row['state'] == 'UNKNOWN':
        end = row['sampled']
    elif row['state'] == 'RESOLVED' and (row['resolution_reason'] or '').startswith('Original guarded runtime terminated'):
        end = row['updated_at']
    else:
        raise ValueError('The end of this UNKNOWN interval needs independent evidence')
    start, gpu = row['unknown_since'], row['gpu_uuid']
    result = {}
    for name, table in TABLES.items():
        result[name] = conn.execute(f'''SELECT
            min(extract(epoch FROM granted-%(start)s)::float8)
                FILTER (WHERE gpu<>%(gpu)s AND admitted >= %(start)s) AS seconds_to_healthy_grant,
            min(extract(epoch FROM granted-%(start)s)::float8)
                FILTER (WHERE gpu<>%(gpu)s) AS seconds_to_any_healthy_grant,
            (SELECT extract(epoch FROM %(start)s-max(granted))::float8 FROM {schema}.grants
                WHERE kind=%(table)s AND gpu<>%(gpu)s AND granted<%(start)s) AS seconds_since_previous_healthy_grant,
            count(*) FILTER (WHERE gpu<>%(gpu)s AND admitted < %(start)s) AS grants_to_inflight_dispatches,
            count(*) FILTER (WHERE gpu<>%(gpu)s AND admitted >= %(start)s) AS healthy_grants,
            count(*) FILTER (WHERE gpu=%(gpu)s) AS isolated_grants
            FROM {schema}.grants WHERE kind=%(table)s AND granted >= %(start)s AND granted < %(end)s''',
            dict(table=table, start=start, end=end, gpu=gpu)).fetchone()
    return dict(start=start.isoformat(), end=end.isoformat(), state=row['state'], traffic=result)


def remove(conn, project):
    schema = project_name(project)
    for table in TABLES.values():
        conn.execute(f'DROP TRIGGER {schema} ON public.{table}')
    conn.execute(f'DROP FUNCTION {schema}.track()')
    conn.execute(f'DROP TABLE {schema}.grants')
    conn.execute(f'DROP SCHEMA {schema}')


def traffic(project, url, seconds):
    import base64
    from collections import Counter
    from concurrent.futures import ThreadPoolExecutor
    from io import BytesIO
    from uuid import uuid4

    import httpx
    from PIL import Image, ImageDraw
    from maas_platform.config import private_text

    if seconds <= 0:
        raise ValueError('Provide a positive duration')
    binding, = [r for r in json.loads(Path(os.environ['MAAS_BINDINGS_FILE']).read_text())['credentials']
                if r['project'] == project_name(project) and r['models'] == ['ocr.default']]
    key = private_text(binding['secret_file'])
    image = Image.new('RGB', (280, 70), 'white')
    ImageDraw.Draw(image).text((15, 15), 'SHARED PP GPU GRANT', fill='black')
    png = BytesIO()
    image.save(png, 'PNG')
    image_data = base64.b64encode(png.getvalue()).decode()
    image_part = {'type': 'image_url', 'image_url': {'url': 'data:image/png;base64,' + image_data}}
    stop = Event()
    until = time.monotonic() + seconds

    def send(kind):
        counts = Counter()
        path = '/maas/v1/ocr' if kind == 'async' else '/gpustack/v1/chat/completions'
        body = (dict(model='ocr.default', image_base64=image_data) if kind == 'async' else
                dict(model='ocr.default', messages=[{'role': 'user', 'content': [image_part]}]))
        with (httpx.Client(base_url=url, trust_env=False, timeout=30, headers={'Authorization': 'Bearer ' + key}) as client,
              psycopg.connect(os.environ['MAAS_DATABASE_URL'], autocommit=True) as db):
            while not stop.is_set() and time.monotonic() < until:
                tick = time.monotonic()
                if kind == 'async':
                    pending = db.execute("""SELECT count(*) FROM tasks WHERE project=%s
                        AND state NOT IN ('FAILED','SUCCEEDED','CANCELLED')""", (project,)).fetchone()[0]
                    if pending >= 2:
                        stop.wait(0.25)
                        continue
                marker = project + '_' + uuid4().hex
                if kind == 'async':
                    body['request_id'] = marker
                try:
                    response = client.post(path, json=body, headers={'Idempotency-Key': marker})
                    counts[str(response.status_code)] += 1
                except httpx.HTTPError as exc:
                    counts[type(exc).__name__] += 1
                stop.wait(max(0, tick + 1 - time.monotonic()))
        return dict(counts)

    with ThreadPoolExecutor(max_workers=2) as pool:
        clients = {k: pool.submit(send, k) for k in TABLES}
        try:
            return {k: future.result() for k, future in clients.items()}
        finally:
            stop.set()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    actions = parser.add_subparsers(dest='action', required=True)
    for action in ('install', 'traffic', 'read', 'remove'):
        sub = actions.add_parser(action)
        sub.add_argument('project', type=project_name)
        if action == 'read':
            sub.add_argument('--kind', choices=TABLES, required=True)
            sub.add_argument('--token', required=True)
            sub.add_argument('--origin-project', help='Project owning the fault, if distinct from the traffic project')
        elif action == 'traffic':
            sub.add_argument('--url', required=True)
            sub.add_argument('--seconds', type=int, required=True)
    args = parser.parse_args()
    if args.action == 'traffic':
        print(json.dumps(traffic(args.project, args.url, args.seconds)))
        raise SystemExit(0)
    with psycopg.connect(os.environ['MAAS_DATABASE_URL'], row_factory=dict_row) as conn:
        if args.action == 'read':
            conn.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY')
            print(json.dumps(read_window(conn, args.project, args.kind, args.token, args.origin_project)))
        elif args.action == 'install':
            install(conn, args.project)
        else:
            remove(conn, args.project)
