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
from .metering import UsageType, select_usage
from .capacity_admission import Admission, configuration, queue_counts, read_budget, unavailable
from .quarantine import blocks, unresolved
from .task_cutover import existing_submission

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
        if media_type == 'application/pdf':
            catalog = self.settings.catalog()
            allowed = [name for name, model in catalog.items()
                       if name in identity.models and model['driver'] == 'gpustack.document_ocr']
            if not allowed:
                raise ServiceError('MEDIA_TYPE', 'PDF upload requires an authorized document model', 415)
            self.auth.authorize(identity, allowed[0], catalog[allowed[0]])
            from .documents import validate_pdf
            validate_pdf(data)
        elif media_type not in ('image/png', 'image/jpeg', 'text/plain', 'application/json', 'application/octet-stream'):
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
        if model['driver'] == 'gpustack.document_ocr':
            updates = {}
            if 'max_attempts' not in body.model_fields_set:
                updates['max_attempts'] = 1
            elif body.max_attempts != 1:
                raise ServiceError('DOCUMENT_REPLAY_UNSAFE', 'Document tasks require max_attempts=1', 422)
            if 'timeout_seconds' not in body.model_fields_set:
                updates['timeout_seconds'] = 900
            elif body.timeout_seconds > 900:
                raise ServiceError('INPUT_INVALID', 'Document task deadline cannot exceed 900 seconds', 422)
            body = body.model_copy(update=updates)
        try:
            inp, params, art_id = adapter_for(model['driver']).validate(body.input, body.parameters)
        except ValidationError:
            raise ServiceError('INPUT_INVALID', 'Input or parameters do not match model schema', 422)
        if model['driver'] == 'gpustack.ocr' and 'image_url' in inp['image']:
            from .task_image_url import submit_url
            return submit_url(self,identity,body,idem,model,inp,params)
        inline = None
        if model['driver'] == 'gpustack.ocr' and 'image_base64' in inp['image']:
            from .consumer_ocr import decode_base64, validate_image
            value = inp['image']['image_base64']
            raw, declared = decode_base64(value, self.settings.max_input_bytes)
            media = validate_image(raw, declared)
            inline = (raw, media)
        return self._accept(identity,body,idem,model,inp,params,art_id,inline)

    def _accept(self, identity, body, idem, model, inp, params, art_id, inline):
        request_hash = fingerprint(body.model_dump())
        with lifecycle_lock(self.store) if inline is not None else nullcontext():
            with self.db.transaction() as c:
                revision = c.execute('SELECT capacity_policy_hash FROM maintenance WHERE id=1 FOR UPDATE').fetchone()['capacity_policy_hash']
                old = existing_submission(c, identity.project, body.model, idem, request_hash)
                if old:
                    return self.public(old), False
                self.store.ensure_space()
                self.check_queue(c, identity.project, body.model, revision)
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
                    if model['driver'] == 'gpustack.document_ocr' and row['media_type'] not in ('application/pdf', 'image/png', 'image/jpeg'):
                        raise ServiceError('MEDIA_TYPE', 'Document task requires a PDF, PNG or JPEG artifact', 415)
                ident = 'task_' + uuid.uuid4().hex
                row = c.execute("""INSERT INTO tasks(id,project,credential_id,idempotency_key,request_hash,model,revision,model_fingerprint,input,parameters,input_artifact_id,state,max_attempts,deadline)
                    VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'QUEUED',%s,now()+%s*interval '1 second') RETURNING *""",
                    (ident, identity.project, identity.credential_id, idem, request_hash, body.model, model['revision'], fingerprint(model), Jsonb(inp), Jsonb(params), art_id, body.max_attempts, body.timeout_seconds)).fetchone()
                return self.public(row), True

    def check_queue(self, c, project, model, revision):
        if revision:
            policy, aliases, local_revision = configuration(self.settings)
            key = aliases.get(model)
            if local_revision != revision or key is None:
                raise unavailable()
            pool = policy.pools[key]
            counts = queue_counts(c, project, list(pool.routes))
            full = counts['pool'] >= pool.queue_limit
        else:
            counts = c.execute("SELECT count(*) total,count(*) FILTER(WHERE project=%s) own "
                               "FROM tasks WHERE state IN ('QUEUED','RETRY_WAIT')", (project,)).fetchone()
            full = False
        if full or counts['total'] >= self.settings.max_queued or counts['own'] >= self.settings.max_project_queued:
            raise ServiceError('QUEUE_FULL', 'Task queue capacity reached', 429, True)

    def usage(self, identity, ident, *, task=False, usage_type: UsageType | None = None):
        with self.db.transaction() as c:
            if task:
                found=c.execute('SELECT id FROM tasks WHERE id=%s AND project=%s',(ident,identity.project)).fetchone()
                if not found:raise ServiceError('NOT_FOUND','Task not found',404)
                row=c.execute("SELECT receipt FROM ocr_usage_records WHERE source_type='task' AND source_id=%s AND project=%s",(ident,identity.project)).fetchone()
            else:
                row=c.execute('SELECT receipt FROM ocr_usage_records WHERE id=%s AND project=%s',(ident,identity.project)).fetchone()
            if not row:raise ServiceError('USAGE_NOT_AVAILABLE','No final OCR usage record is available',404)
            return select_usage(row['receipt'], usage_type)

    def get(self, identity, ident):
        with self.db.transaction() as c:
            row = c.execute("""SELECT t.*, u.receipt AS usage
                FROM tasks t LEFT JOIN ocr_usage_records u
                  ON u.project=t.project AND u.source_type='task' AND u.source_id=t.id
                  AND t.state='SUCCEEDED'
                WHERE t.id=%s AND t.project=%s""",
                (ident, identity.project)).fetchone()
            if not row: raise ServiceError('NOT_FOUND', 'Task not found', 404)
            return {**self.public(row), 'usage': row['usage']}

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

    def claim(self, *, legacy_slot=True, automatic_slot=True):
        with self.db.transaction() as c:
            maintenance = c.execute('SELECT * FROM maintenance WHERE id=1 FOR UPDATE').fetchone()
            revision = maintenance['capacity_policy_hash']
            if revision:
                self.expire_queued(c)
            if maintenance['paused']: return None
            self.expire_sync(c)
            unknown = unresolved(c)
            if any(row['dispatch_route'] is None for row in unknown):
                return None
            catalog = self.settings.catalog() if unknown else {}
            if revision:
                admission = Admission(c, self.settings, revision, include_preparing=True)
                if not admission.budget or not (automatic_slot if admission.automatic else legacy_slot):
                    return None
                ready = "state IN ('QUEUED','RETRY_WAIT') AND next_run<=clock_timestamp() AND deadline>clock_timestamp()"
                pairs = c.execute(f'SELECT DISTINCT project, model FROM tasks WHERE {ready}').fetchall()
                eligible = [(r['project'], r['model']) for r in pairs
                            if admission.allows(r['model'], r['project'], asynchronous=True)
                            and admission.dispatch(r['model'],
                                catalog.get(r['model'], {}).get('upstream_model', ''), unknown)[2] is None]
                if not eligible:
                    return None
                projects, models = zip(*eligible)
                row = c.execute(f'''SELECT * FROM tasks WHERE {ready} AND (project,model) IN
                    (SELECT * FROM unnest(%s::text[],%s::text[]))
                    ORDER BY created_at,id FOR UPDATE SKIP LOCKED LIMIT 1''', (list(projects), list(models))).fetchone()
                if not admission.fresh(c):
                    return None
            else:
                if not legacy_slot:
                    return None
                self.expire_queued(c)
                running = c.execute('SELECT count(*) AS n FROM inference_slots').fetchone()['n']
                if running >= self.settings.max_running: return None
                blocked = {row['model'] for row in unknown}
                blocked.update(name for name, model in catalog.items()
                               if blocks(unknown, name, model.get('upstream_model'), None))
                row = c.execute("""SELECT * FROM tasks t WHERE state IN ('QUEUED','RETRY_WAIT') AND next_run<=now() AND deadline>now()
                    AND NOT (t.model=ANY(%s::text[]))
                    AND (SELECT count(*) FROM inference_slots s WHERE s.project=t.project) < %s
                    ORDER BY created_at FOR UPDATE OF t SKIP LOCKED LIMIT 1""", (list(blocked), self.settings.max_project_running,)).fetchone()
            if not row: return None
            token = uuid.uuid4().hex
            row = c.execute("UPDATE tasks SET state='RUNNING',attempts=attempts+1,token=%s,lease_until=now()+%s*interval '1 second',updated_at=now() WHERE id=%s RETURNING *", (token, self.settings.lease_seconds, row['id'])).fetchone()
            c.execute("INSERT INTO task_attempts(token,task_id,attempt,state) VALUES(%s,%s,%s,'RUNNING')", (token, row['id'], row['attempts']))
            return row

    def begin_backend(self, task, draining=None):
        """Persist intent before sending bytes; an uncertain commit never permits a send."""
        with self.db.transaction() as c:
            maintenance = c.execute('SELECT * FROM maintenance WHERE id=1 FOR UPDATE').fetchone()
            if maintenance['paused']:
                raise ServiceError('MAINTENANCE_PAUSED', 'Inference dispatch is paused', 503)
            self.expire_sync(c)
            row = c.execute("""SELECT *, deadline>clock_timestamp() timely FROM tasks WHERE id=%s AND token=%s
                AND state IN ('RUNNING','CANCEL_REQUESTED') AND lease_until>clock_timestamp() FOR UPDATE""", (task['id'], task['token'])).fetchone()
            if not row:
                raise ServiceError('INTERRUPTED', 'Attempt is no longer eligible for dispatch')
            model = self.model(row['model'])
            if fingerprint(model) != row['model_fingerprint']:
                raise ServiceError('MODEL_REVISION_CHANGED', 'Registered model configuration changed')
            route = model.get('upstream_model') or ''
            pool_id = gpu = None
            revision = maintenance['capacity_policy_hash']
            allowed = row['timely'] and row['state'] == 'RUNNING'
            unknown = unresolved(c)
            reason = 'BACKEND_QUARANTINED' if blocks(unknown, row['model'], route, None) else None
            if revision and allowed:
                try:
                    admission = Admission(c, self.settings, revision, include_preparing=False)
                    pool_id = admission.pool_ids.get(row['model'])
                    allowed = admission.allows(row['model'], row['project'], asynchronous=True) and admission.fresh(c)
                    route, gpu, reason = admission.dispatch(row['model'], route, unknown)
                except ServiceError as exc:
                    if exc.code != 'CAPACITY_UNAVAILABLE':
                        raise
                    allowed = False
            if draining is not None and draining():
                raise ServiceError('EXECUTOR_DRAINING', 'Executor is draining', 503)
            if allowed and reason is None:
                inserted = c.execute('''INSERT INTO backend_activity(token,task_id,state,dispatch_route,dispatch_pool_id,
                    execution_guard,dispatch_gpu_uuid)
                    SELECT token,id,'DISPATCHING',%s,%s,%s,%s FROM tasks WHERE id=%s
                    AND lease_until>clock_timestamp() AND deadline>clock_timestamp()''',
                    (route, pool_id, model.get('execution_guard', False), gpu, task['id'])).rowcount
                if inserted:
                    return route
            if not (revision or reason) or not self._defer(c, row, reason or 'CAPACITY_REDUCED'):
                raise ServiceError('INTERRUPTED', 'Attempt is no longer eligible for dispatch')
        raise ServiceError('DISPATCH_DEFERRED', 'Unsent dispatch has been deferred', 503, True)

    @staticmethod
    def expire_queued(c):
        c.execute("UPDATE tasks SET state='FAILED',error_code='DEADLINE_EXCEEDED',updated_at=now() "
                  "WHERE state IN ('QUEUED','RETRY_WAIT') AND deadline<=clock_timestamp()")

    def complete_backend(self, task):
        # The owning request proved completion for this exact transport attempt.
        with self.db.transaction() as c:
            c.execute('SELECT id FROM maintenance WHERE id=1 FOR UPDATE')
            c.execute("""UPDATE backend_activity SET state='COMPLETED',updated_at=now(),
                      resolution_reason=CASE WHEN state='UNKNOWN' THEN 'Transport confirmed completion' ELSE resolution_reason END
                      WHERE token=%s AND task_id=%s AND NOT execution_guard AND state IN ('DISPATCHING','UNKNOWN')""", (task['token'], task['id']))

    def defer_dispatch(self, task, reason='MAINTENANCE_PAUSED'):
        """Return an unsent attempt to the queue without spending an execution retry."""
        if reason not in ('MAINTENANCE_PAUSED', 'EXECUTOR_DRAINING'):
            raise ValueError('Unsupported unsent deferral reason')
        with self.db.transaction() as c:
            c.execute('SELECT id FROM maintenance WHERE id=1 FOR UPDATE')
            row = c.execute("SELECT * FROM tasks WHERE id=%s AND token=%s AND state IN ('RUNNING','CANCEL_REQUESTED') AND lease_until>now() FOR UPDATE", (task['id'],task['token'])).fetchone()
            return bool(row and self._defer(c, row, reason))

    @staticmethod
    def _defer(c, row, reason):
        if c.execute('SELECT 1 FROM backend_activity WHERE token=%s', (row['token'],)).fetchone():
            return False
        expired = c.execute('SELECT %s<=clock_timestamp() expired', (row['deadline'],)).fetchone()['expired']
        state = 'CANCELLED' if row['state']=='CANCEL_REQUESTED' else ('FAILED' if expired else 'QUEUED')
        code = 'DEADLINE_EXCEEDED' if state=='FAILED' else None
        c.execute("UPDATE task_attempts SET state='DEFERRED',error_code=%s,ended_at=now() WHERE token=%s", (reason,row['token']))
        delay = 1 if reason in ('CAPACITY_REDUCED', 'BACKEND_QUARANTINED') else 0
        c.execute("UPDATE tasks SET state=%s,error_code=%s,token=NULL,lease_until=NULL,next_run=now()+%s*interval '1 second',updated_at=now() WHERE id=%s", (state,code,delay,row['id']))
        return True

    def set_capacity_mode(self, enforce):
        with self.db.transaction() as c:
            row = c.execute('SELECT paused FROM maintenance WHERE id=1 FOR UPDATE').fetchone()
            if not row['paused'] or self.has_unknown(c) or c.execute('''SELECT 1 FROM tasks
                WHERE state IN ('RUNNING','CANCEL_REQUESTED') UNION ALL SELECT 1 FROM sync_activity
                WHERE state='DISPATCHING' LIMIT 1''').fetchone():
                raise ServiceError('MAINTENANCE_REQUIRED', 'Pause and drain inference before switching capacity mode', 409)
            revision = None
            if enforce:
                policy, _, revision = configuration(self.settings)
                budget = read_budget(c, revision)
                if not all(pool.gateway_verified for pool in policy.pools.values()) or not budget or not budget['global']['total']:
                    raise unavailable()
            mode = 'enforce' if enforce else 'static'
            result = c.execute('''UPDATE maintenance SET capacity_policy_hash=%s, generation=generation+1,
                updated_at=now() WHERE id=1 RETURNING generation''', (revision,)).fetchone()
            c.execute("INSERT INTO maintenance_events(generation,action,reason) VALUES(%s,%s,%s)",
                      (result['generation'], 'capacity-'+mode, 'Capacity admission mode changed'))
            return mode

    def resolve_backend(self, token, reason):
        if len(reason.strip()) < 10:
            raise ValueError('Provide evidence of backend termination (at least 10 characters)')
        with self.db.transaction() as c:
            c.execute('SELECT id FROM maintenance WHERE id=1 FOR UPDATE')
            return bool(c.execute("UPDATE backend_activity SET state='RESOLVED',resolution_reason=%s,updated_at=now() WHERE token=%s AND state='UNKNOWN' RETURNING token", (reason,token)).fetchone())

    def heartbeat(self, task):
        return bool(self.heartbeat_many((task,)))

    def heartbeat_many(self, tasks):
        if not tasks:
            return 0
        ids, tokens = zip(*[(task['id'], task['token']) for task in tasks])
        with self.db.transaction() as c:
            return c.execute('''UPDATE tasks t SET lease_until=clock_timestamp()+%s*interval '1 second'
                FROM unnest(%s::text[], %s::text[]) AS a(id, token)
                WHERE t.id=a.id AND t.token=a.token AND t.state IN ('RUNNING','CANCEL_REQUESTED')
                  AND t.lease_until>clock_timestamp()''',
                (self.settings.lease_seconds, list(ids), list(tokens))).rowcount

    def cancelled(self, task):
        with self.db.transaction() as c:
            row = c.execute('SELECT state,token,deadline,lease_until FROM tasks WHERE id=%s', (task['id'],)).fetchone()
            return (not row or row['token'] != task['token'] or row['state'] != 'RUNNING'
                    or row['deadline'] <= datetime.now(timezone.utc) or row['lease_until'] <= datetime.now(timezone.utc))

    @serialized_artifacts
    def finish(self, task, output=None, error=None, metering=None):
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
                if state=='SUCCEEDED' and metering is not None:
                    from .metering import persist
                    if metering['source_type']!='task' or metering['source_id']!=row['id']:
                        raise ValueError('Usage source mismatch')
                    persist(c,row['project'],metering)
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
        c.execute("""UPDATE sync_activity SET state=CASE WHEN gpu_finished_at IS NULL THEN 'UNKNOWN' ELSE 'COMPLETED' END,
                     updated_at=now() WHERE state='DISPATCHING' AND deadline<=clock_timestamp()""")

    @staticmethod
    def has_unknown(c):
        return bool(c.execute("SELECT 1 FROM backend_activity WHERE state='UNKNOWN' UNION ALL SELECT 1 FROM sync_activity WHERE state='UNKNOWN' LIMIT 1").fetchone())

    def metrics(self):
        with self.db.transaction() as c:
            rows = c.execute('SELECT state,count(*) AS n FROM tasks GROUP BY state').fetchall()
            events = c.execute('SELECT count(*) AS n FROM task_usage_events').fetchone()['n']
            paused = c.execute('SELECT paused FROM maintenance WHERE id=1').fetchone()['paused']
            unknown = c.execute("SELECT count(*) n FROM backend_activity WHERE state='UNKNOWN'").fetchone()['n']
            sync_unknown = c.execute("SELECT count(*) n FROM sync_activity WHERE state='UNKNOWN' OR (state='DISPATCHING' AND deadline<=now() AND gpu_finished_at IS NULL)").fetchone()['n']
        return {'tasks': {r['state']:r['n'] for r in rows}, 'successful_tasks': events, 'dispatch_paused': paused, 'unknown_backend_activity': unknown, 'unknown_sync_activity':sync_unknown}
