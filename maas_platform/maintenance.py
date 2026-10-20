"""Host-only maintenance coordination. Drained HTTP work is not a GPU stop proof."""
from .errors import ServiceError


class Maintenance:
    def __init__(self, service):
        self.service=service

    def _snapshot(self,c):
        row=c.execute('SELECT * FROM maintenance WHERE id=1 FOR UPDATE').fetchone()
        self.service.expire_sync(c)
        tasks=c.execute("SELECT id,model,state FROM tasks WHERE state IN ('RUNNING','CANCEL_REQUESTED') ORDER BY id").fetchall()
        backend=c.execute("SELECT token,task_id,state FROM backend_activity WHERE state IN ('DISPATCHING','UNKNOWN') ORDER BY token").fetchall()
        sync=c.execute("SELECT token,model,state FROM sync_activity WHERE state IN ('DISPATCHING','UNKNOWN') ORDER BY token").fetchall()
        return {'paused':row['paused'],'generation':row['generation'],'reason':row['reason'],
                'active_tasks':tasks,'backend_activity':backend,'sync_activity':sync,
                'managed_requests_drained':bool(row['paused'] and not (tasks or backend or sync)),
                'safe_to_poweroff':False,
                'external_boundary':'must close direct GPUStack, Playground and worker inference access separately'}

    def status(self):
        with self.service.db.transaction() as c:return self._snapshot(c)

    def resume(self,probe):
        # Probe executes outside the transaction; a generation fence prevents a
        # concurrent new maintenance window from being reopened by its late result.
        before=self.status()
        if not before['managed_requests_drained']:
            raise ServiceError('MAINTENANCE_NOT_DRAINED','Pause and resolve all managed activity before resuming',409)
        probe()  # Must raise on failed preflight or real inference; never swallow it.
        with self.service.db.transaction() as c:
            after=self._snapshot(c)
            if after['generation']!=before['generation'] or not after['managed_requests_drained']:
                raise ServiceError('MAINTENANCE_CHANGED','Maintenance state changed during recovery probe',409)
            row=c.execute("UPDATE maintenance SET paused=false,generation=generation+1,updated_at=now() WHERE id=1 RETURNING generation").fetchone()
            c.execute("INSERT INTO maintenance_events(generation,action,reason) VALUES(%s,'verified-resume','Trusted preflight and inference probe passed')",(row['generation'],))
        return self.status()

    def resolve_sync(self,token,reason):
        if len(reason.strip())<10:raise ValueError('Provide backend termination evidence (at least 10 characters)')
        with self.service.db.transaction() as c:
            c.execute('SELECT id FROM maintenance WHERE id=1 FOR UPDATE')
            self.service.expire_sync(c)
            return bool(c.execute("UPDATE sync_activity SET state='RESOLVED',resolution_reason=%s,updated_at=now() WHERE token=%s AND state='UNKNOWN' RETURNING token",(reason,token)).fetchone())
