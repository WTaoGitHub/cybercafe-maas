"""Single-control-host E1 routing ledger and maintenance state machine.

No sockets, SSH, subprocesses or deployment writes. Operator hooks supply actual
boundary/process/probe evidence. The task queue and billing remain in PostgreSQL;
this private bounded journal stores dispatch intents only. Not a public API.
"""
from contextlib import contextmanager
import fcntl
import json
import math
import os
from pathlib import Path
import time
import uuid

from ..collector import atomic_json
from ..errors import ServiceError
from .topology import fresh


class E1Control:
    def __init__(self, path, clock=time.time):
        self.path = Path(path)
        self.clock = clock

    @contextmanager
    def transaction(self):
        # A stable lock inode serializes threads AND processes across rename.
        with open(str(self.path)+'.lock', 'a+b') as lock:
            os.chmod(lock.name, 0o600)
            fcntl.flock(lock, fcntl.LOCK_EX)
            s = json.loads(self.path.read_text())
            if s['schema_version'] != 1:
                raise ValueError('Unknown E1 journal schema')
            yield s
            atomic_json(self.path, s)

    @classmethod
    def create(cls, path, models, worker_slots, global_slots=2, project_slots=1,
               protected_workers=('001',), max_records=10000, recovery_hold_seconds=10, clock=time.time):
        # Explicit expected placements let a failed observation disappear without
        # losing which models a target worker was meant to serve.
        if not models or not worker_slots or not set(protected_workers) <= set(worker_slots):
            raise ValueError('Explicit model placements and protected control workers required')
        limits = [global_slots, project_slots, max_records, *worker_slots.values()]
        if any(type(n) is not int or n <= 0 for n in limits) or project_slots > global_slots:
            raise ValueError('Invalid E1 capacity limits')
        if not math.isfinite(recovery_hold_seconds) or recovery_hold_seconds < 0:
            raise ValueError('Invalid recovery hold')
        for m in models.values():
            if (not m['revision'] or not m['image_id'].startswith('sha256:') or not m['workers']
                    or len(set(m['workers'])) != len(m['workers'])
                    or not set(m['workers']) <= set(worker_slots)):
                raise ValueError('Invalid E1 model policy')
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        state = dict(schema_version=1, models=models, replicas=[], published_at=0,
            workers={w:{'mode':'ACTIVE','session':None} for w in worker_slots}, worker_slots=worker_slots,
            global_slots=global_slots, project_slots=project_slots, protected_workers=list(protected_workers),
            max_records=max_records, recovery_hold_seconds=recovery_hold_seconds,
            requests={}, ambiguous={}, events=[])
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, 'w') as f:
            json.dump(state, f, allow_nan=False); f.flush(); os.fsync(f.fileno())
        return cls(path, clock)

    def event(self, s, action, **fields):
        # Fixed-size operational history; request intents are never auto-pruned.
        s['events'] = (s['events'] + [dict(at=self.clock(), action=action, **fields)])[-1000:]

    def expire(self, s):
        for r in s['requests'].values():
            if r['state'] == 'DISPATCHING' and r['deadline'] <= self.clock():
                r['state'] = 'UNKNOWN'
                self.event(s, 'REQUEST_UNKNOWN', token=r['token'], worker=r['worker'])

    def publish(self, replicas):
        with self.transaction() as s:
            seen, placements, rows = set(), set(), []
            for replica in replicas:
                r = replica.public()
                m = s['models'].get(r['logical_model'])
                if (not m or r['revision'] != m['revision'] or r['image_id'] != m['image_id']
                        or r['worker'] not in m['workers'] or r['route'] in seen
                        or (r['logical_model'],r['worker']) in placements
                        or type(r['slots']) is not int or r['slots'] <= 0
                        or not fresh(r['observed_at'], self.clock())):
                    raise ValueError('Unapproved or stale E1 topology')
                seen.add(r['route']); placements.add((r['logical_model'],r['worker'])); rows.append(r)
            s['replicas'] = rows
            s['published_at'] = self.clock()
            self.event(s, 'TOPOLOGY_PUBLISHED', routes=len(rows))

    def eligible(self, s, model, exclude=()):
        unknown = {r['worker'] for r in s['requests'].values() if r['state'] == 'UNKNOWN'
                   or (r['state'] == 'DISPATCHING' and r['deadline'] <= self.clock())}
        if s['ambiguous']:
            return []  # legacy/multi-worker sends retain the global conservative fence
        return [r for r in s['replicas'] if r['logical_model'] == model
            and r['worker'] not in exclude and r['worker'] not in unknown
            and s['workers'][r['worker']]['mode'] == 'ACTIVE'
            and r['logical_model']+'@'+r['worker'] not in s.get('model_isolations', {})
            and fresh(r['observed_at'], self.clock())]

    @staticmethod
    def active(s):
        return [r for r in s['requests'].values() if r['state'] in ('DISPATCHING', 'UNKNOWN')]

    def snapshot(self):
        with self.transaction() as s:
            self.expire(s)
            return json.loads(json.dumps(s))

    def capacity(self, model):
        with self.transaction() as s:
            self.expire(s)
            if model not in s['models']:
                raise ServiceError('MODEL_NOT_FOUND', 'Unknown logical model', 404)
            rows = self.eligible(s, model)
            return {'model':model, 'healthy_slots':sum(r['slots'] for r in rows),
                    'workers':sorted({r['worker'] for r in rows})}

    def reserve(self, token, project, model, revision, kind, deadline):
        if (not token or len(token)>160 or not project or len(project)>128
                or kind not in ('sync', 'async') or not math.isfinite(deadline)
                or not self.clock() < deadline <= self.clock()+3600):
            raise ValueError('Invalid dispatch identity or deadline')
        # Expiration is a separate committed transaction, so a rejected request
        # cannot roll back discovering an expired remote execution.
        self.snapshot()
        with self.transaction() as s:
            if token in s['requests']:
                raise ServiceError('E1_DISPATCH_EXISTS', 'Attempt already reserved; never resend', 409)
            if model not in s['models'] or revision != s['models'][model]['revision']:
                raise ServiceError('MODEL_REVISION_CHANGED', 'Logical model revision is not approved', 409)
            rows = self.eligible(s, model)
            if not rows:
                raise ServiceError('E1_MODEL_UNAVAILABLE', 'No verified healthy replica', 503)
            active = self.active(s)
            if (len(s['requests']) >= s['max_records'] or len(active) >= s['global_slots']
                    or sum(r['project'] == project for r in active) >= s['project_slots']):
                raise ServiceError('E1_CAPACITY', 'Shared dispatch capacity reached', 429, True)
            def route_load(r):
                return sum(x['route'] == r['route'] for x in active)
            rows = [r for r in rows if route_load(r)<r['slots']
                    and sum(x['worker']==r['worker'] for x in active)<s['worker_slots'][r['worker']]]
            if not rows:
                raise ServiceError('E1_CAPACITY', 'Remaining worker capacity reached', 429, True)
            r = min(rows, key=lambda r:(route_load(r), r['worker'], r['route']))
            request = dict(token=token, project=project, model=model, revision=revision, kind=kind,
                worker=r['worker'], route=r['route'], instance_id=r['instance_id'],
                configuration_sha256=r['configuration_sha256'], image_id=r['image_id'],
                deadline=deadline, reserved_at=self.clock(), state='DISPATCHING')
            # Persist BEFORE returning a route to a transport. If commit raises,
            # caller must not send; crash after this point remains conservatively
            # unresolved even if no bytes were sent.
            s['requests'][token] = request
            self.event(s, 'REQUEST_RESERVED', token=token, worker=r['worker'], kind=kind)
        return dict(request)

    def finish(self, token, confirmed):
        with self.transaction() as s:
            r = s['requests'][token]
            if r['state'] in ('COMPLETED', 'RESOLVED'):
                return dict(r)
            r['state'] = 'COMPLETED' if confirmed else 'UNKNOWN'
            r['finished_at'] = self.clock()
            self.event(s, 'REQUEST_'+r['state'], token=token, worker=r['worker'])
            return dict(r)

    def ambiguous(self, token, reason):
        # Called for legacy requests whose potential execution set isn't proven.
        with self.transaction() as s:
            s['ambiguous'][token] = reason
            self.event(s, 'AMBIGUOUS_ACTIVITY', token=token)

    def resolve(self, token, evidence):
        if len(evidence.strip()) < 10:
            raise ValueError('Actual completion/termination evidence required')
        with self.transaction() as s:
            if token in s['ambiguous']:
                del s['ambiguous'][token]
            else:
                r = s['requests'][token]
                if r['state'] != 'UNKNOWN':
                    raise ValueError('Only unknown activity may be resolved')
                r.update(state='RESOLVED', resolution=evidence)
            self.event(s, 'ACTIVITY_RESOLVED', token=token, evidence=evidence)

    def isolate(self, worker, reason, model=None):
        """Fault isolation need not wait for spare capacity; never confirms stop.

        model!=None revokes only that model's aliases on this worker. A known
        worker-wide failure closes the worker. Restored observations do not
        reopen it; explicit recovery and probes are required.
        """
        with self.transaction() as s:
            if worker not in s['workers']:
                raise ValueError('Unapproved worker')
            if model is not None:
                if model not in s['models'] or worker not in s['models'][model]['workers']:
                    raise ValueError('Unapproved model placement')
                # Instance failures share a worker quarantine if any execution
                # is unknown; model-only quarantine itself is persisted below.
                s.setdefault('model_isolations', {})[model+'@'+worker] = dict(
                    reason=reason,session=uuid.uuid4().hex,changed_at=self.clock())
            else:
                w = s['workers'][worker]
                w.update(mode='ISOLATED', session=w.get('session') or uuid.uuid4().hex,
                         changed_at=self.clock(), reason=reason)
            self.event(s, 'FAULT_ISOLATED', worker=worker, model=model, reason=reason)

    def begin(self, worker, reason):
        self.snapshot()
        with self.transaction() as s:
            if worker in s['protected_workers']:
                raise ServiceError('E1_CONTROL_HOST', 'Control host requires a separate migration plan', 409)
            w = s['workers'][worker]
            if w['mode'] != 'ACTIVE':
                raise ServiceError('E1_SESSION_CONFLICT', 'Inspect the existing worker session', 409)
            for model, policy in s['models'].items():
                if worker in policy['workers'] and not self.eligible(s, model, exclude=(worker,)):
                    raise ServiceError('E1_NO_SPARE', 'Maintenance would remove the last healthy model replica', 409)
            session = uuid.uuid4().hex
            w.update(mode='DRAINING', session=session, changed_at=self.clock(), reason=reason)
            self.event(s, 'WORKER_DRAINING', worker=worker, session=session)
            return session

    def window(self, s, worker, session):
        w = s['workers'][worker]
        if not session or w['session'] != session:
            raise ServiceError('E1_SESSION_CONFLICT', 'Worker session has changed', 409)
        return w

    def drained(self, worker, session):
        with self.transaction() as s:
            self.expire(s)
            self.window(s, worker, session)
            active = [r for r in self.active(s) if r['worker'] == worker]
            return not active and not s['ambiguous']

    def stop(self, worker, session, boundary, stop_hook):
        # Hooks must be independently scoped operator adapters. Persist intent
        # before invocation; a crash cannot silently re-run an uncertain stop.
        self.snapshot()
        with self.transaction() as s:
            w = self.window(s, worker, session)
            if worker in s['protected_workers'] or w['mode'] not in ('DRAINING', 'ISOLATED'):
                raise ServiceError('E1_STOP_PHASE', 'Worker is not eligible for a scoped stop', 409)
            if any(r['worker']==worker for r in self.active(s)) or s['ambiguous']:
                raise ServiceError('E1_NOT_DRAINED', 'Unresolved activity prevents declaring the worker idle', 409)
            self.evidence(boundary, worker, session, w['changed_at'])
            if not all(boundary.get(k) is True for k in ('bypass_closed','scheduling_blocked','route_withdrawn')):
                raise ServiceError('E1_BOUNDARY_UNPROVEN', 'All entry and scheduling paths must be fenced', 409)
            w.update(mode='STOPPING', changed_at=self.clock(), boundary=boundary)
            self.event(s, 'STOP_INTENT', worker=worker, session=session)
        try:
            proof = stop_hook(worker, session)
            with self.transaction() as s:
                w = self.window(s, worker, session)
                self.evidence(proof, worker, session, w['changed_at'])
                if not all(proof.get(k) is True for k in ('worker_stopped','instances_stopped','gpu_idle')):
                    raise ServiceError('E1_STOP_UNCONFIRMED', 'Worker stop alone does not prove GPU idle', 409)
                w.update(mode='STOPPED', stop_proof=proof, changed_at=self.clock())
                self.event(s, 'WORKER_STOPPED', worker=worker, session=session)
        except Exception:
            with self.transaction() as s:
                w = self.window(s, worker, session)
                w.update(mode='STOP_UNCONFIRMED', changed_at=self.clock())
                self.event(s, 'STOP_UNCONFIRMED', worker=worker, session=session)
            raise

    def evidence(self, proof, worker, session, after=0):
        if (proof.get('worker') != worker or proof.get('session') != session
                or not fresh(proof.get('observed_at'), self.clock())
                or proof['observed_at'] < after):
            raise ServiceError('E1_EVIDENCE_INVALID', 'Fresh worker/session evidence required', 409)

    def recover(self, worker, session, restore_hook, probe_hook):
        with self.transaction() as s:
            w = self.window(s, worker, session)
            if w['mode'] not in ('STOPPED','STOP_UNCONFIRMED','STOPPING','ISOLATED','DRAINING','RECOVERING'):
                raise ServiceError('E1_RECOVERY_PHASE', 'Worker is not isolated', 409)
            w.update(mode='RECOVERING', recovery_started=self.clock())
            self.event(s, 'RECOVERY_INTENT', worker=worker, session=session)
        # Restore hook must be idempotent and must retain the external fence.
        # Exceptions leave RECOVERING; no automatic route rejoin or UNKNOWN clear.
        restore_hook(worker, session)
        with self.transaction() as s:
            self.expire(s)
            w = self.window(s, worker, session)
            if any(r['worker']==worker for r in self.active(s)) or s['ambiguous']:
                raise ServiceError('E1_NOT_DRAINED', 'Resolve residual activities before rejoin', 409)
            if self.clock()-w['changed_at'] < s['recovery_hold_seconds']:
                raise ServiceError('E1_RECOVERY_HOLD', 'Recovery stability hold has not elapsed', 409)
            rows = [r for r in s['replicas'] if r['worker']==worker and fresh(r['observed_at'],self.clock())
                    and r['observed_at']>=w['recovery_started']]
            expected = {m for m,p in s['models'].items() if worker in p['workers']}
            if {r['logical_model'] for r in rows} != expected:
                raise ServiceError('E1_RECOVERY_UNVERIFIED', 'Every expected model requires fresh version evidence', 409)
            checked = [{k:r[k] for k in ('route','revision','image_id','instance_id',
                                        'configuration_sha256','logical_model')} for r in rows]
        # Never hold the global ledger lock during network calls: healthy workers
        # must keep accepting requests while recovery probes are in flight.
        for r in rows:
            token = 'probe:'+uuid.uuid4().hex
            with self.transaction() as s:
                w = self.window(s, worker, session)
                if w['mode'] != 'RECOVERING':
                    raise ServiceError('E1_SESSION_CONFLICT', 'Recovery was superseded', 409)
                active = self.active(s)
                if (s['ambiguous'] or len(active)>=s['global_slots']
                        or sum(x['worker']==worker for x in active)>=s['worker_slots'][worker]
                        or len(s['requests'])>=s['max_records']):
                    raise ServiceError('E1_CAPACITY', 'Recovery probe capacity reached', 429, True)
                s['requests'][token] = dict(token=token, project='$operator-probe', model=r['logical_model'],
                    revision=r['revision'], kind='probe', worker=worker, route=r['route'],
                    instance_id=r['instance_id'], deadline=self.clock()+30,
                    reserved_at=self.clock(), state='DISPATCHING')
                self.event(s, 'PROBE_RESERVED', token=token, worker=worker)
            try:
                proof = probe_hook(dict(r), session)
                self.evidence(proof, worker, session)
                if (proof.get('ok') is not True or any(proof.get(k)!=r[k]
                        for k in ('route','revision','image_id','instance_id'))):
                    raise ServiceError('E1_RECOVERY_PROBE', 'Real alias probe failed or target changed', 409)
            except BaseException:
                self.finish(token, False)
                raise
            else:
                self.finish(token, True)
        with self.transaction() as s:
            self.expire(s)
            w = self.window(s, worker, session)
            current = [r for r in s['replicas'] if r['worker']==worker and fresh(r['observed_at'],self.clock())]
            identities = [{k:r[k] for k in ('route','revision','image_id','instance_id',
                                          'configuration_sha256','logical_model')} for r in current]
            if (w['mode'] != 'RECOVERING' or identities != checked or s['ambiguous']
                    or any(r['worker']==worker for r in self.active(s))):
                raise ServiceError('E1_RECOVERY_UNVERIFIED', 'Topology or residual activity changed during probes', 409)
            w.update(mode='ACTIVE', session=None, changed_at=self.clock())
            for model in expected:
                s.get('model_isolations', {}).pop(model+'@'+worker, None)
            self.event(s, 'WORKER_REJOINED', worker=worker, session=session)

    def recover_model(self, worker, model, session, probe_hook):
        key = model+'@'+worker
        with self.transaction() as s:
            self.expire(s)
            fault = s.get('model_isolations', {}).get(key)
            if not fault or fault['session'] != session or s['workers'][worker]['mode'] != 'ACTIVE':
                raise ServiceError('E1_SESSION_CONFLICT', 'Model recovery session is not eligible', 409)
            if (s['ambiguous'] or any(r['worker']==worker for r in self.active(s))
                    or self.clock()-fault['changed_at']<s['recovery_hold_seconds']):
                raise ServiceError('E1_NOT_DRAINED', 'Residual activity or stability hold prevents model recovery', 409)
            rows = [r for r in s['replicas'] if r['worker']==worker and r['logical_model']==model
                    and fresh(r['observed_at'],self.clock())]
            if len(rows)!=1:
                raise ServiceError('E1_RECOVERY_UNVERIFIED', 'A fresh single model alias is required', 409)
            r = rows[0]
            token = 'probe:'+uuid.uuid4().hex
            if len(self.active(s))>=s['global_slots'] or len(s['requests'])>=s['max_records']:
                raise ServiceError('E1_CAPACITY', 'Recovery probe capacity reached', 429, True)
            s['requests'][token] = dict(token=token, project='$operator-probe', model=model,
                revision=r['revision'], kind='probe', worker=worker, route=r['route'],
                instance_id=r['instance_id'], deadline=self.clock()+30,
                reserved_at=self.clock(), state='DISPATCHING')
            self.event(s, 'MODEL_PROBE_RESERVED', token=token, worker=worker, model=model)
        try:
            proof = probe_hook(dict(r), session)
            self.evidence(proof,worker,session)
            if proof.get('ok') is not True or any(proof.get(k)!=r[k]
                    for k in ('route','revision','image_id','instance_id')):
                raise ServiceError('E1_RECOVERY_PROBE', 'Model alias probe did not verify', 409)
        except BaseException:
            self.finish(token,False)
            raise
        else:
            self.finish(token,True)
        with self.transaction() as s:
            fault = s.get('model_isolations', {}).get(key)
            fields = ('route','revision','image_id','instance_id','configuration_sha256')
            current = [x for x in s['replicas'] if x['worker']==worker and x['logical_model']==model
                       and fresh(x['observed_at'],self.clock())]
            if (not fault or fault['session']!=session or s['workers'][worker]['mode']!='ACTIVE'
                    or len(current)!=1 or any(current[0][k]!=r[k] for k in fields) or s['ambiguous']
                    or any(x['worker']==worker for x in self.active(s))):
                raise ServiceError('E1_RECOVERY_UNVERIFIED', 'Model recovery evidence was superseded', 409)
            del s['model_isolations'][key]
            self.event(s,'MODEL_REJOINED',worker=worker,model=model,session=session)
