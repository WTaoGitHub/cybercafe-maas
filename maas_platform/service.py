import hashlib, json, uuid
from contextlib import nullcontext
from datetime import datetime, timezone
from psycopg.types.json import Jsonb
from pydantic import ValidationError
from .artifacts import ArtifactStore, serialized_artifacts, lifecycle_lock
from .db import Database
from .errors import ServiceError
from .adapters import adapter_for
from .artifact_gc import ArtifactGC

ACTIVE = ('QUEUED', 'RETRY_WAIT', 'RUNNING', 'CANCEL_REQUESTED')


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False, allow_nan=False)


def fingerprint(value):
    return hashlib.sha256(canonical(value).encode()).hexdigest()


class TaskService:
    def __init__(self, settings, auth):
        self.settings, self.auth = settings, auth
        self.db = Database(settings.database_url)
        self.store = ArtifactStore(settings)
        self.gc = ArtifactGC(self)

    def model(self, name):
        value = self.settings.catalog().get(name)
        if value is None:
            raise ServiceError('MODEL_NOT_FOUND', 'Model capability not registered', 404)
        return value

    def check_storage(self, c, project, size):
        # Called under the cross-process artifact lifecycle lock, including commit.
        row=c.execute("SELECT coalesce(sum(size),0) total, coalesce(sum(size) FILTER (WHERE project=%s),0) own FROM artifacts WHERE deleted_at IS NULL",(project,)).fetchone()
        if row['total']+size>self.settings.max_storage_bytes or row['own']+size>self.settings.max_project_storage_bytes:
            raise ServiceError('STORAGE_QUOTA','Artifact storage quota reached; clean expired files or raise quota',429,True)

    @serialized_artifacts
    def upload(self, identity, data, media_type):
        if not data or len(data) > self.settings.max_input_bytes:
            raise ServiceError('INPUT_SIZE', 'Empty or oversized input', 413)
        if media_type not in ('image/png', 'image/jpeg', 'text/plain', 'application/json', 'application/octet-stream'):
            raise ServiceError('MEDIA_TYPE', 'Unsupported upload media type', 415)
        try:
            with self.db.transaction() as c:
                self.check_storage(c, identity.project, len(data))
                record = self.store.put(data)
                c.execute("INSERT INTO artifacts(id,project,kind,media_type,size,sha256,expires_at) VALUES(%s,%s,'input',%s,%s,%s,now()+%s*interval '1 second')",
                          (record['id'], identity.project, media_type, record['size'], record['sha256'], self.settings.input_ttl_seconds))
        except Exception:
            # Commit acknowledgement can be lost after PostgreSQL committed. Keep the
            # durable file rather than delete an input that may already be referenced.
            # Unreferenced files are reconciled by the later orphan-cleanup work.
            raise
        return record

    def submit(self, identity, body, idem):
        if not idem or len(idem) > 128:
            raise ServiceError('IDEMPOTENCY_REQUIRED', 'Supply Idempotency-Key (1..128 characters)')
        model = self.model(body.model); self.auth.authorize(identity, body.model, model)
        try:
            inp, params, art_id = adapter_for(model['driver']).validate(body.input, body.parameters)
        except ValidationError:
            raise ServiceError('INPUT_INVALID', 'Input or parameters do not match model schema', 422)
        if model['driver'] == 'gpustack.ocr' and 'url' in inp['image']:
            from .task_image_url import submit_url
            return submit_url(self,identity,body,idem,model,inp,params)
        inline = None
        if model['driver'] == 'gpustack.ocr' and 'data_url' in inp['image']:
            from .consumer_ocr import decode_base64, validate_image
            value = inp['image']['data_url']
            if not value.startswith(('data:image/png;base64,','data:image/jpeg;base64,')):
                raise ServiceError('IMAGE_BASE64', 'Use a PNG or JPEG base64 data URL', 422)
            raw, declared = decode_base64(value, self.settings.max_input_bytes)
            media = validate_image(raw, declared)
            inline = (raw, media)
        return self._accept(identity,body,idem,model,inp,params,art_id,inline)

    def _accept(self, identity, body, idem, model, inp, params, art_id, inline):
        request_hash = fingerprint(body.model_dump())
        self.store.ensure_space()
        with lifecycle_lock(self.store) if inline is not None else nullcontext():
            with self.db.transaction() as c:
                c.execute('SELECT id FROM maintenance WHERE id=1 FOR UPDATE')
                old = c.execute('SELECT * FROM tasks WHERE project=%s AND idempotency_key=%s', (identity.project, idem)).fetchone()
                if old:
                    if old['request_hash'] != request_hash:
                        raise ServiceError('IDEMPOTENCY_CONFLICT', 'Key already used for different content', 409)
                    return self.public(old), False
                total = c.execute("SELECT count(*) AS n FROM tasks WHERE state IN ('QUEUED','RETRY_WAIT')").fetchone()['n']
                own = c.execute("SELECT count(*) AS n FROM tasks WHERE project=%s AND state IN ('QUEUED','RETRY_WAIT')", (identity.project,)).fetchone()['n']
                if total >= self.settings.max_queued or own >= self.settings.max_project_queued:
                    raise ServiceError('QUEUE_FULL', 'Task queue capacity reached', 429, True)
                if inline is not None:
                    raw, media = inline
                    self.check_storage(c, identity.project, len(raw))
                    record = self.store.put(raw)
                    c.execute("INSERT INTO artifacts(id,project,kind,media_type,size,sha256,expires_at) VALUES(%s,%s,'input',%s,%s,%s,now()+%s*interval '1 second')",
                              (record['id'], identity.project, media, record['size'], record['sha256'], self.settings.input_ttl_seconds))
                    art_id = record['id']
                    inp = {'image': {'artifact_id': art_id}}
                    # Never delete on an ambiguous commit; orphan GC reconciles unreferenced files.
                if art_id:
                    row = c.execute('SELECT * FROM artifacts WHERE id=%s AND project=%s AND kind=\'input\' AND deleted_at IS NULL AND expires_at>now() FOR UPDATE', (art_id, identity.project)).fetchone()
                    if not row:
                        raise ServiceError('INPUT_NOT_FOUND', 'Input artifact unavailable', 404)
                ident = 'task_' + uuid.uuid4().hex
                row = c.execute("""INSERT INTO tasks(id,project,credential_id,idempotency_key,request_hash,model,revision,model_fingerprint,input,parameters,input_artifact_id,state,max_attempts,deadline)
                    VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'QUEUED',%s,now()+%s*interval '1 second') RETURNING *""",
                    (ident, identity.project, identity.credential_id, idem, request_hash, body.model, model['revision'], fingerprint(model), Jsonb(inp), Jsonb(params), art_id, body.max_attempts, body.timeout_seconds)).fetchone()
                return self.public(row), True

    def get(self, identity, ident):
        with self.db.transaction() as c:
            row = c.execute('SELECT * FROM tasks WHERE id=%s AND project=%s', (ident, identity.project)).fetchone()
            if not row: raise ServiceError('NOT_FOUND', 'Task not found', 404)
            return self.public(row)

    @staticmethod
    def public(row):
        return {k: row[k] for k in ('id','model','revision','state','attempts','result_artifact_id','error_code','deadline','created_at','updated_at')}

    def cancel(self, identity, ident):
        with self.db.transaction() as c:
            row = c.execute('SELECT * FROM tasks WHERE id=%s AND project=%s FOR UPDATE', (ident, identity.project)).fetchone()
            if not row: raise ServiceError('NOT_FOUND', 'Task not found', 404)
            if row['state'] in ('QUEUED','RETRY_WAIT'):
                state = 'CANCELLED'
            elif row['state'] == 'RUNNING': state = 'CANCEL_REQUESTED'
            else: state = row['state']
            updated = c.execute('UPDATE tasks SET state=%s,updated_at=now() WHERE id=%s RETURNING *', (state, ident)).fetchone()
            return self.public(updated)

    def artifact(self, identity, ident):
        with self.db.transaction() as c:
            row = c.execute('SELECT * FROM artifacts WHERE id=%s AND project=%s FOR SHARE', (ident, identity.project)).fetchone()
            if not row: raise ServiceError('NOT_FOUND', 'Artifact not found', 404)
            if row['deleted_at'] or row['expires_at'] <= datetime.now(timezone.utc):
                raise ServiceError('RESULT_EXPIRED', 'Artifact expired', 410)
            try: data = self.store.path(ident).read_bytes()
            except OSError: raise ServiceError('ARTIFACT_UNAVAILABLE', 'Artifact file unavailable', 503, True)
            if len(data) != row['size'] or hashlib.sha256(data).hexdigest() != row['sha256']:
                raise ServiceError('ARTIFACT_CORRUPT', 'Artifact integrity check failed', 503)
            return data, row['media_type']

    def read_task_input(self, task):
        # An active task pins its input even if the upload TTL has elapsed.
        with self.db.transaction() as c:
            row = c.execute("""SELECT a.* FROM artifacts a JOIN tasks t ON t.input_artifact_id=a.id
                WHERE t.id=%s AND t.token=%s AND t.project=a.project
                AND t.state IN ('RUNNING','CANCEL_REQUESTED') AND t.lease_until>now()
                AND a.deleted_at IS NULL FOR SHARE OF a""", (task['id'],task['token'])).fetchone()
            if not row: raise ServiceError('INPUT_NOT_FOUND','Pinned task input unavailable',404)
            try: data = self.store.path(row['id']).read_bytes()
            except OSError: raise ServiceError('ARTIFACT_UNAVAILABLE','Input unavailable',503,True)
            if len(data)!=row['size'] or hashlib.sha256(data).hexdigest()!=row['sha256']:
                raise ServiceError('ARTIFACT_CORRUPT','Input integrity check failed',503)
            return data,row['media_type']

    def claim(self):
        with self.db.transaction() as c:
            maintenance = c.execute('SELECT * FROM maintenance WHERE id=1 FOR UPDATE').fetchone()
            if maintenance['paused']: return None
            self.expire_sync(c)
            if self.has_unknown(c): return None
            c.execute("UPDATE tasks SET state='FAILED',error_code='DEADLINE_EXCEEDED',updated_at=now() WHERE state IN ('QUEUED','RETRY_WAIT') AND deadline<=now()")
            running = c.execute("SELECT (SELECT count(*) FROM tasks WHERE state IN ('RUNNING','CANCEL_REQUESTED')) + (SELECT count(*) FROM sync_activity WHERE state='DISPATCHING') AS n").fetchone()['n']
            if running >= self.settings.max_running: return None
            row = c.execute("""SELECT * FROM tasks t WHERE state IN ('QUEUED','RETRY_WAIT') AND next_run<=now() AND deadline>now()
                AND (SELECT count(*) FROM tasks r WHERE r.project=t.project AND r.state IN ('RUNNING','CANCEL_REQUESTED')) + (SELECT count(*) FROM sync_activity s WHERE s.project=t.project AND s.state='DISPATCHING') < %s
                ORDER BY created_at FOR UPDATE OF t SKIP LOCKED LIMIT 1""", (self.settings.max_project_running,)).fetchone()
            if not row: return None
            token = uuid.uuid4().hex
            row = c.execute("UPDATE tasks SET state='RUNNING',attempts=attempts+1,token=%s,lease_until=now()+%s*interval '1 second',updated_at=now() WHERE id=%s RETURNING *", (token, self.settings.lease_seconds, row['id'])).fetchone()
            c.execute("INSERT INTO task_attempts(token,task_id,attempt,state) VALUES(%s,%s,%s,'RUNNING')", (token, row['id'], row['attempts']))
            return row

    def begin_backend(self, task):
        """Persist intent before sending bytes; an uncertain commit never permits a send."""
        with self.db.transaction() as c:
            maintenance = c.execute('SELECT * FROM maintenance WHERE id=1 FOR UPDATE').fetchone()
            if maintenance['paused']:
                raise ServiceError('MAINTENANCE_PAUSED', 'Inference dispatch is paused', 503)
            self.expire_sync(c)
            if self.has_unknown(c):
                raise ServiceError('BACKEND_QUARANTINED', 'Unresolved backend activity blocks dispatch', 503)
            row = c.execute("SELECT id FROM tasks WHERE id=%s AND token=%s AND state='RUNNING' AND lease_until>now() AND deadline>now() FOR UPDATE", (task['id'], task['token'])).fetchone()
            if not row:
                raise ServiceError('INTERRUPTED', 'Attempt is no longer eligible for dispatch')
            c.execute("INSERT INTO backend_activity(token,task_id,state) VALUES(%s,%s,'DISPATCHING')", (task['token'],task['id']))

    def complete_backend(self, task):
        # Only a fully received successful inference response proves remote completion.
        with self.db.transaction() as c:
            c.execute("UPDATE backend_activity SET state='COMPLETED',updated_at=now() WHERE token=%s AND state IN ('DISPATCHING','UNKNOWN')", (task['token'],))

    def defer_dispatch(self, task, reason='MAINTENANCE_PAUSED'):
        """Return an unsent attempt to the queue without spending an execution retry."""
        if reason not in ('MAINTENANCE_PAUSED', 'EXECUTOR_DRAINING'):
            raise ValueError('Unsupported unsent deferral reason')
        with self.db.transaction() as c:
            c.execute('SELECT id FROM maintenance WHERE id=1 FOR UPDATE')
            row = c.execute("SELECT * FROM tasks WHERE id=%s AND token=%s AND state IN ('RUNNING','CANCEL_REQUESTED') AND lease_until>now() FOR UPDATE", (task['id'],task['token'])).fetchone()
            if not row or c.execute('SELECT 1 FROM backend_activity WHERE token=%s', (task['token'],)).fetchone():
                return False
            state = 'CANCELLED' if row['state']=='CANCEL_REQUESTED' else ('FAILED' if row['deadline']<=datetime.now(timezone.utc) else 'QUEUED')
            code = 'DEADLINE_EXCEEDED' if state=='FAILED' else None
            c.execute("UPDATE task_attempts SET state='DEFERRED',error_code=%s,ended_at=now() WHERE token=%s", (reason,task['token']))
            c.execute('UPDATE tasks SET state=%s,error_code=%s,token=NULL,lease_until=NULL,next_run=now(),updated_at=now() WHERE id=%s', (state,code,row['id']))
            return True

    def resolve_backend(self, token, reason):
        if len(reason.strip()) < 10:
            raise ValueError('Provide evidence of backend termination (at least 10 characters)')
        with self.db.transaction() as c:
            return bool(c.execute("UPDATE backend_activity SET state='RESOLVED',resolution_reason=%s,updated_at=now() WHERE token=%s AND state='UNKNOWN' RETURNING token", (reason,token)).fetchone())

    def heartbeat(self, task):
        with self.db.transaction() as c:
            return bool(c.execute("UPDATE tasks SET lease_until=now()+%s*interval '1 second' WHERE id=%s AND token=%s AND state IN ('RUNNING','CANCEL_REQUESTED') AND lease_until>now() RETURNING id", (self.settings.lease_seconds, task['id'], task['token'])).fetchone())

    def cancelled(self, task):
        with self.db.transaction() as c:
            row = c.execute('SELECT state,token,deadline,lease_until FROM tasks WHERE id=%s', (task['id'],)).fetchone()
            return (not row or row['token'] != task['token'] or row['state'] != 'RUNNING'
                    or row['deadline'] <= datetime.now(timezone.utc) or row['lease_until'] <= datetime.now(timezone.utc))

    @serialized_artifacts
    def finish(self, task, output=None, error=None):
        result = None
        preserve_result_file = False
        try:
            with self.db.transaction() as c:
                if output is not None:
                    data=canonical(output).encode()
                    if len(data)>self.settings.max_output_bytes:
                        error=ServiceError('OUTPUT_SIZE','Output too large')
                    else:
                        self.check_storage(c,task['project'],len(data))
                        result=self.store.put(data)
                # Serialize quarantine visibility with claim/begin_backend decisions.
                c.execute('SELECT id FROM maintenance WHERE id=1 FOR UPDATE')
                row = c.execute('SELECT * FROM tasks WHERE id=%s FOR UPDATE', (task['id'],)).fetchone()
                if (not row or row['token'] != task['token'] or row['state'] not in ('RUNNING','CANCEL_REQUESTED')
                        or row['lease_until'] <= datetime.now(timezone.utc)):
                    return False
                activity = c.execute("SELECT state FROM backend_activity WHERE token=%s FOR UPDATE", (task['token'],)).fetchone()
                if activity and activity['state'] in ('DISPATCHING','UNKNOWN'):
                    c.execute("UPDATE backend_activity SET state='UNKNOWN',updated_at=now() WHERE token=%s", (task['token'],))
                    error = ServiceError('UNKNOWN_BACKEND_ACTIVITY', 'Remote completion is unconfirmed; dispatch quarantined')
                state, code, result_id = 'FAILED', error.code if error else None, None
                if activity and activity['state'] in ('DISPATCHING','UNKNOWN'):
                    state, code = 'FAILED', 'UNKNOWN_BACKEND_ACTIVITY'
                elif row['state'] == 'CANCEL_REQUESTED': state, code = 'CANCELLED', None
                elif row['deadline'] <= datetime.now(timezone.utc): code = 'DEADLINE_EXCEEDED'
                elif error:
                    used = c.execute("SELECT count(*) n FROM task_attempts WHERE task_id=%s AND state!='DEFERRED'", (row['id'],)).fetchone()['n']
                    if error.retryable and used < row['max_attempts']: state = 'RETRY_WAIT'
                elif result:
                    state, result_id = 'SUCCEEDED', result['id']
                    c.execute("INSERT INTO artifacts(id,project,kind,media_type,size,sha256,expires_at) VALUES(%s,%s,'result','application/json',%s,%s,now()+%s*interval '1 second')", (result_id, row['project'], result['size'], result['sha256'], self.settings.result_ttl_seconds))
                    c.execute('INSERT INTO task_usage_events(task_id,project,quantity) VALUES(%s,%s,1)', (row['id'], row['project']))
                else: code = 'NO_RESULT'
                c.execute("UPDATE tasks SET state=%s,error_code=%s,result_artifact_id=%s,lease_until=NULL,token=NULL,next_run=now()+%s*interval '1 second',updated_at=now() WHERE id=%s", (state, code, result_id, min(2**row['attempts'],30), row['id']))
                c.execute('UPDATE task_attempts SET state=%s,error_code=%s,ended_at=now() WHERE token=%s', (state, code, row['token']))
                preserve_result_file = result_id is not None
            return True
        finally:
            if result and not preserve_result_file: self.store.remove(result['id'])

    def recover(self):
        # Durable dispatch intent fences remote work; local pre-dispatch work may retry.
        with self.db.transaction() as c:
            c.execute('SELECT id FROM maintenance WHERE id=1 FOR UPDATE')
            rows = c.execute("SELECT * FROM tasks WHERE state IN ('RUNNING','CANCEL_REQUESTED') AND lease_until<=now() FOR UPDATE SKIP LOCKED").fetchall()
            for row in rows:
                activity = c.execute("SELECT state FROM backend_activity WHERE token=%s FOR UPDATE", (row['token'],)).fetchone()
                if activity and activity['state'] in ('DISPATCHING','UNKNOWN'):
                    c.execute("UPDATE backend_activity SET state='UNKNOWN',updated_at=now() WHERE token=%s", (row['token'],))
                    state, code = 'FAILED', 'UNKNOWN_BACKEND_ACTIVITY'
                elif activity and activity['state']=='COMPLETED': state, code = 'FAILED', 'RESULT_NOT_COMMITTED'
                elif row['state'] == 'CANCEL_REQUESTED': state, code = 'CANCELLED', None
                elif row['deadline'] <= datetime.now(timezone.utc): state, code = 'FAILED', 'DEADLINE_EXCEEDED'
                elif c.execute("SELECT count(*) n FROM task_attempts WHERE task_id=%s AND state!='DEFERRED'", (row['id'],)).fetchone()['n'] >= row['max_attempts']: state, code = 'FAILED', 'ATTEMPTS_EXHAUSTED'
                else: state, code = 'RETRY_WAIT', 'LEASE_EXPIRED'
                c.execute("UPDATE task_attempts SET state='LEASE_EXPIRED',error_code=%s,ended_at=now() WHERE token=%s", (code, row['token']))
                c.execute('UPDATE tasks SET state=%s,error_code=%s,token=NULL,lease_until=NULL,next_run=now(),updated_at=now() WHERE id=%s', (state, code, row['id']))
            return len(rows)

    def cleanup(self, *, blocking=True):
        return self.gc.expired(blocking=blocking)

    def cleanup_orphans(self, *, blocking=True):
        return self.gc.orphans(blocking=blocking)

    def pause(self, paused, reason=''):
        with self.db.transaction() as c:
            row=c.execute('UPDATE maintenance SET paused=%s,reason=%s,generation=generation+1,updated_at=now() WHERE id=1 RETURNING generation', (paused, reason)).fetchone()
            c.execute('INSERT INTO maintenance_events(generation,action,reason) VALUES(%s,%s,%s)', (row['generation'],'pause' if paused else 'resume',reason))

    @staticmethod
    def expire_sync(c):
        c.execute("UPDATE sync_activity SET state='UNKNOWN',updated_at=now() WHERE state='DISPATCHING' AND deadline<=now()")

    @staticmethod
    def has_unknown(c):
        return bool(c.execute("SELECT 1 FROM backend_activity WHERE state='UNKNOWN' UNION ALL SELECT 1 FROM sync_activity WHERE state='UNKNOWN' LIMIT 1").fetchone())

    def metrics(self):
        with self.db.transaction() as c:
            rows = c.execute('SELECT state,count(*) AS n FROM tasks GROUP BY state').fetchall()
            events = c.execute('SELECT count(*) AS n FROM task_usage_events').fetchone()['n']
            paused = c.execute('SELECT paused FROM maintenance WHERE id=1').fetchone()['paused']
            unknown = c.execute("SELECT count(*) n FROM backend_activity WHERE state='UNKNOWN'").fetchone()['n']
            sync_unknown = c.execute("SELECT count(*) n FROM sync_activity WHERE state='UNKNOWN' OR (state='DISPATCHING' AND deadline<=now())").fetchone()['n']
        return {'tasks': {r['state']:r['n'] for r in rows}, 'successful_tasks': events, 'dispatch_paused': paused, 'unknown_backend_activity': unknown, 'unknown_sync_activity':sync_unknown}
