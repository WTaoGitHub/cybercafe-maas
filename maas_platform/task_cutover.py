"""Private, drain-first transfer of task submission ownership between task databases."""
import hashlib
import re
from psycopg.types.json import Jsonb

from .errors import ServiceError


def existing_submission(c, project, model, key, request_hash, previous_default_hash=None):
    old = c.execute('SELECT * FROM tasks WHERE project=%s AND idempotency_key=%s', (project, key)).fetchone()
    if old:
        if old['request_hash'] not in (request_hash, previous_default_hash):
            raise ServiceError('IDEMPOTENCY_CONFLICT', 'Key already used for different content', 409)
        return old
    key_hash = hashlib.sha256(key.encode()).hexdigest()
    if c.execute('SELECT 1 FROM task_external_keys WHERE project=%s AND key_hash=%s', (project, key_hash)).fetchone():
        raise ServiceError('IDEMPOTENCY_OTHER_STORE', 'Key belongs to a task at its original entry point', 409)
    if c.execute('SELECT 1 FROM task_submission_freezes WHERE model=%s', (model,)).fetchone():
        raise ServiceError('MODEL_SUBMISSIONS_CLOSED', 'New submissions are closed at this entry point', 503, True)
    return None


class TaskCutover:
    def __init__(self, db):
        self.db = db

    @staticmethod
    def lock(c):
        # Same lock as TaskService._accept and dispatch. No network I/O under it.
        c.execute('SELECT id FROM maintenance WHERE id=1 FOR UPDATE')

    @staticmethod
    def frozen_and_drained(c, model):
        if not c.execute('SELECT 1 FROM task_submission_freezes WHERE model=%s', (model,)).fetchone():
            raise ServiceError('MODEL_NOT_FROZEN', 'Freeze model submissions before transferring task keys', 409)
        if c.execute('''SELECT 1 FROM tasks WHERE model=%s AND state IN ('QUEUED','RUNNING','RETRY_WAIT','CANCEL_REQUESTED')
            UNION ALL SELECT 1 FROM backend_activity b JOIN tasks t ON t.id=b.task_id
                WHERE t.model=%s AND b.state IN ('DISPATCHING','UNKNOWN')
            UNION ALL SELECT 1 FROM sync_activity WHERE model=%s AND state IN ('DISPATCHING','UNKNOWN') LIMIT 1''',
                     (model, model, model)).fetchone():
            raise ServiceError('MODEL_NOT_DRAINED', 'Pending or unconfirmed execution blocks transfer', 409)

    def freeze(self, model):
        with self.db.transaction() as c:
            self.lock(c)
            c.execute('INSERT INTO task_submission_freezes(model) VALUES(%s) ON CONFLICT DO NOTHING', (model,))

    def export(self, model):
        with self.db.transaction() as c:
            self.lock(c)
            self.frozen_and_drained(c, model)
            rows = c.execute('''SELECT project, encode(sha256(convert_to(idempotency_key,'UTF8')),'hex') AS key_hash,
                                id AS task_id FROM tasks WHERE model=%s ORDER BY project,key_hash''', (model,)).fetchall()
            return {'version': 1, 'model': model, 'keys': rows}

    def activate(self, model, manifest):
        if not isinstance(manifest, dict) or set(manifest) != {'version','model','keys'} or (
                type(manifest['version']) is not int or manifest['version'] != 1
                or manifest['model'] != model or not isinstance(manifest['keys'], list)):
            raise ValueError('Invalid task cutover manifest')
        seen = set()
        for row in manifest['keys']:
            if not isinstance(row, dict) or set(row) != {'project','key_hash','task_id'} or not all(
                    isinstance(v, str) and v and '\0' not in v for v in row.values()) or not (
                    re.fullmatch('[0-9a-f]{64}', row['key_hash']) and re.fullmatch('task_[0-9a-f]{32}', row['task_id'])):
                raise ValueError('Invalid task cutover key')
            entry = (row['project'], row['key_hash'])
            if entry in seen:
                raise ValueError('Duplicate task cutover key')
            seen.add(entry)
        rows = Jsonb(manifest['keys'])
        with self.db.transaction() as c:
            self.lock(c)
            self.frozen_and_drained(c, model)
            if c.execute('''WITH incoming AS (SELECT * FROM jsonb_to_recordset(%s)
                AS i(project text,key_hash text,task_id text))
                SELECT 1 FROM incoming i JOIN tasks t ON t.project=i.project
                    AND encode(sha256(convert_to(t.idempotency_key,'UTF8')),'hex')=i.key_hash
                UNION ALL SELECT 1 FROM incoming i JOIN task_external_keys e USING(project,key_hash)
                    WHERE e.task_id<>i.task_id LIMIT 1''', (rows,)).fetchone():
                raise ServiceError('CUTOVER_KEY_CONFLICT', 'A destination project key belongs to a different task', 409)
            c.execute('''INSERT INTO task_external_keys(project,key_hash,task_id)
                SELECT project,key_hash,task_id FROM jsonb_to_recordset(%s) AS i(project text,key_hash text,task_id text)
                ON CONFLICT DO NOTHING''', (rows,))
            c.execute('DELETE FROM task_submission_freezes WHERE model=%s', (model,))


def main(argv=None):
    import argparse
    import json

    from .config import Settings
    from .db import Database
    from .operator_config import create_private, private_json

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('freeze', 'export', 'activate'))
    parser.add_argument('--model', required=True)
    parser.add_argument('--manifest')
    args = parser.parse_args(argv)
    if not args.model or args.model != args.model.strip():
        parser.error('--model must be the exact logical model name')
    if args.action != 'freeze' and not args.manifest:
        parser.error('--manifest is required for export and activate')
    cutover = TaskCutover(Database(Settings.from_env().database_url))
    try:
        if args.action == 'freeze':
            cutover.freeze(args.model)
            result = {'submissions': 'frozen'}
        elif args.action == 'export':
            value = cutover.export(args.model)
            create_private(args.manifest, value)
            result = {'exported_keys': len(value['keys'])}
        else:
            cutover.activate(args.model, private_json(args.manifest))
            result = {'submissions': 'open'}
    except (ServiceError, OSError, ValueError) as exc:
        parser.exit(1, (exc.code if isinstance(exc, ServiceError) else 'TASK_CUTOVER_INPUT_INVALID') + '\n')
    print(json.dumps(result))


if __name__ == '__main__':
    main()
