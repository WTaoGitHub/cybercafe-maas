"""Temporary benchmark adapters. No migrations, SSH polling or background service."""
import re
import hashlib
import threading
import time

import psycopg
from psycopg.rows import dict_row


class DatabaseObserver:
    def __init__(self, dsn, project, model, revision, prefix, statement_ms=200):
        if not re.fullmatch(r'e3-[a-zA-Z0-9-]{8,64}:',prefix):
            raise ValueError('Use a fresh e3-<run-id>: namespace')
        if not all(isinstance(v,str) and v for v in (project,model,revision)):
            raise ValueError('Explicit project/model/revision required')
        if type(statement_ms) is not int or not 10<=statement_ms<=1000:
            raise ValueError('Bounded observer statement timeout required')
        self.scope=(project,prefix,model,revision)
        self.lock=threading.Lock()
        self.connection=psycopg.connect(dsn,autocommit=True,row_factory=dict_row,connect_timeout=5,
            options=f'-c default_transaction_read_only=on -c statement_timeout={statement_ms} -c lock_timeout={statement_ms}',
            application_name='maas-e3-temporary-observer')

    def close(self):
        self.connection.close()

    def snapshot(self,include_ids=False):
        # One MVCC snapshot for activity, counters, IDs and timestamp. In
        # particular, updated_at is NOT used as a commit/measurement clock.
        # Active tasks pin their inputs even after upload TTL, so a pinned
        # artifact remains valid provided it has not been deleted.
        with self.lock:
            row=self.connection.execute("""
            WITH scope AS MATERIALIZED (
              SELECT * FROM tasks WHERE project=%s AND starts_with(idempotency_key,%s)
            ), own AS MATERIALIZED (SELECT * FROM scope WHERE model=%s AND revision=%s),
            counts AS (SELECT
              count(*) FILTER(WHERE t.state IN ('QUEUED','RETRY_WAIT') AND t.next_run<=now()
                AND t.deadline>now() AND (t.input_artifact_id IS NULL OR EXISTS(
                  SELECT 1 FROM artifacts a WHERE a.id=t.input_artifact_id
                  AND a.project=t.project AND a.kind='input' AND a.deleted_at IS NULL)))::int ready,
              count(*) FILTER(WHERE t.state IN ('FAILED','CANCELLED','CANCEL_REQUESTED'))::int failed,
              count(*) FILTER(WHERE t.state='SUCCEEDED')::int successes,
              count(*) FILTER(WHERE t.state IN ('QUEUED','RETRY_WAIT'))::int own_queued,
              count(*) FILTER(WHERE t.state IN ('RUNNING','CANCEL_REQUESTED'))::int own_running,
              count(*) FILTER(WHERE t.max_attempts<>1)::int retry_policy_drift,
              CASE WHEN %s THEN array_agg(t.id ORDER BY t.id) FILTER(WHERE t.state='SUCCEEDED') END success_ids
              FROM own t)
            SELECT extract(epoch FROM statement_timestamp())::float8 db_at, counts.*,
              (SELECT count(*)::int FROM task_usage_events e JOIN own t ON t.id=e.task_id) metered,
              (SELECT count(*)::int FROM scope)-(SELECT count(*)::int FROM own) scope_drift,
              (SELECT count(*)::int FROM tasks WHERE state IN ('QUEUED','RETRY_WAIT')) global_queued,
              (SELECT count(*)::int FROM tasks WHERE project=%s AND state IN ('QUEUED','RETRY_WAIT')) project_queued,
              (SELECT count(*)::int FROM tasks WHERE state IN ('RUNNING','CANCEL_REQUESTED')) running,
              (SELECT count(*)::int FROM tasks WHERE project=%s AND state IN ('RUNNING','CANCEL_REQUESTED')) project_running,
              (SELECT count(*)::int FROM sync_activity WHERE state='DISPATCHING') sync_running,
              (SELECT count(*)::int FROM sync_activity WHERE project=%s AND state='DISPATCHING') project_sync_running,
              (SELECT count(*)::int FROM backend_activity WHERE state='UNKNOWN') unknown,
              (SELECT count(*)::int FROM sync_activity WHERE state='UNKNOWN') sync_unknown,
              maintenance.paused,maintenance.generation
            FROM counts CROSS JOIN maintenance WHERE maintenance.id=1
            """,(*self.scope,include_ids,*([self.scope[0]]*3))).fetchone()
        if not row:raise RuntimeError('Missing maintenance singleton')
        if row.pop('scope_drift') or row.pop('retry_policy_drift'):
            raise RuntimeError('Benchmark namespace/model/retry policy drift')
        if row.pop('metered')!=row['successes']:raise RuntimeError('Committed task/metering mismatch')
        row['success_ids']=row['success_ids'] or []
        return row

    def reconcile(self,task_ids):
        with self.lock:
            return self.connection.execute("""SELECT t.id,t.state,t.attempts,t.result_artifact_id,
              (SELECT count(*)::int FROM task_usage_events e WHERE e.task_id=t.id) events
              FROM tasks t WHERE t.id=ANY(%s) AND t.project=%s AND starts_with(t.idempotency_key,%s)
              AND t.model=%s AND t.revision=%s ORDER BY t.id""",(list(task_ids),*self.scope)).fetchall()

    def fresh(self):
        with self.lock:
            count=self.connection.execute("SELECT count(*) n FROM tasks WHERE project=%s AND starts_with(idempotency_key,%s)",self.scope[:2]).fetchone()['n']
        if count:raise RuntimeError('Run namespace already exists; no resume/replay')


class TaskBackend:
    """The supplied HTTP client has bounded timeouts and transport retries=0.

    Input fixtures are already uploaded. Only acceptance happens during the
    window; task reconciliation/result verification belongs to the drain phase.
    """
    def __init__(self,client,observer,fixtures,timeout_seconds=120):
        if not fixtures or not all(isinstance(f,dict) and isinstance(f.get('input'),dict) for f in fixtures):
            raise ValueError('Prepared input fixtures required')
        if type(timeout_seconds) is not int or not 1<=timeout_seconds<=3600:raise ValueError('Invalid task deadline')
        self.client=client;self.observer=observer;self.fixtures=fixtures;self.timeout=timeout_seconds
        self.last_download_seconds=0.

    def prepare(self):
        self.observer.fresh()
        project,prefix,model,revision=self.observer.scope
        response=self.client.get('/maas/v1/models/'+model+'/capabilities')
        if response.status_code!=200 or response.json().get('revision')!=revision:
            raise RuntimeError('Capability/revision preflight failed')
        for fixture in self.fixtures:
            ident=fixture['input'].get('artifact_id')
            if ident:
                response=self.client.get('/maas/v1/artifacts/'+ident)
                if response.status_code!=200 or hashlib.sha256(response.content).hexdigest()!=fixture.get('sha256'):
                    raise RuntimeError('Prepared input artifact checksum/readability failed')

    def snapshot(self,include_ids=False):
        row=self.observer.snapshot(include_ids)
        if row['global_queued']!=row['own_queued'] or row['running']!=row['own_running'] or row['sync_running']:
            raise RuntimeError('Foreign traffic during pure async experiment')
        return row

    def submit(self,sequence):
        project,prefix,model,revision=self.observer.scope
        fixture=self.fixtures[sequence%len(self.fixtures)]
        response=self.client.post('/maas/v1/tasks',headers={'Idempotency-Key':prefix+str(sequence)},json={
            'model':model,'input':fixture['input'],'parameters':fixture.get('parameters',{}),
            'timeout_seconds':self.timeout,'max_attempts':1})
        if response.status_code!=202:raise RuntimeError('Task acceptance failed; inspect intent, do not replay')
        row=response.json()
        if row.get('model')!=model or row.get('revision')!=revision or not row.get('id'):
            raise RuntimeError('Unexpected acknowledgement identity')
        return row['id']

    def reconcile(self,task_ids):return self.observer.reconcile(task_ids)

    def verify_results(self,rows):
        started=time.monotonic()
        for row in rows:
            artifact=row['result_artifact_id']
            if not artifact:raise RuntimeError('Missing result artifact')
            response=self.client.get('/maas/v1/artifacts/'+artifact)
            if response.status_code!=200 or not isinstance(response.json(),dict):
                raise RuntimeError('Result download/integrity verification failed')
        self.last_download_seconds=time.monotonic()-started
        return {'downloaded_results':len(rows),'download_seconds':self.last_download_seconds}
