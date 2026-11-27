"""Host-only maintenance coordination. Drained HTTP work is not a GPU stop proof."""
from .errors import ServiceError
from .capacity_admission import read_budget


class Maintenance:
    def __init__(self, service):
        self.service=service

    def _snapshot(self,c):
        row=c.execute('SELECT * FROM maintenance WHERE id=1 FOR UPDATE').fetchone()
        self.service.expire_sync(c)
        tasks=c.execute("SELECT id,model,state FROM tasks WHERE state IN ('RUNNING','CANCEL_REQUESTED') ORDER BY id").fetchall()
        runtime_columns = """b.gpustack_routed,extract(epoch FROM clock_timestamp()-b.created_at)::float8 AS dispatch_age_seconds,
            extract(epoch FROM clock_timestamp()-b.unknown_since)::float8 AS unknown_age_seconds,
            r.last_checked_at::text AS runtime_checked_at,r.last_outcome AS runtime_outcome,
            r.terminal_proof->>'nonce' AS termination_evidence"""
        backend = c.execute(f"""SELECT b.token,b.task_id,t.model,b.state,b.dispatch_route,b.dispatch_pool_id,
            b.dispatch_gpu_uuid,b.gpu_uuid,b.isolated_gpu_uuid,b.isolation_evidence,b.guard_scope,b.guard_conflict,
            b.resolution_reason,{runtime_columns}
            FROM backend_activity b JOIN tasks t ON t.id=b.task_id
            LEFT JOIN execution_runtime r ON r.instance=b.runtime_instance
            WHERE b.state IN ('DISPATCHING','UNKNOWN') ORDER BY b.token""").fetchall()
        sync = c.execute(f"""SELECT b.token,b.model,b.state,b.dispatch_route,b.dispatch_pool_id,b.dispatch_gpu_uuid,
            b.gpu_uuid,b.isolated_gpu_uuid,b.isolation_evidence,b.guard_scope,b.guard_conflict,b.resolution_reason,
            {runtime_columns}
            FROM sync_activity b LEFT JOIN execution_runtime r ON r.instance=b.runtime_instance
            WHERE b.state IN ('DISPATCHING','UNKNOWN') ORDER BY b.token""").fetchall()
        for activity in backend + sync:
            if activity['state'] != 'UNKNOWN':
                continue
            if row['record_unknown_only'] or activity['gpustack_routed']:
                reason = 'record_only'
            elif activity['guard_conflict']:
                reason = 'guard_conflict'
            elif activity['termination_evidence']:
                reason = 'termination_evidence_verified'
            elif (activity['unknown_age_seconds'] or 0) >= 900:
                reason = 'termination_evidence_required'
            elif activity['gpu_uuid'] and (activity['guard_scope'] or activity['gpustack_routed']):
                reason = 'awaiting_gpu_finish'
            else:
                reason = 'termination_evidence_required'
            activity['pending_reason'] = reason
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
        def resume_state(c):
            state = self._snapshot(c)
            revision = c.execute('SELECT capacity_policy_hash FROM maintenance WHERE id=1').fetchone()['capacity_policy_hash']
            budget = read_budget(c, revision) if revision else None
            if budget and budget.get('source') == 'gpustack_ready':
                idle = not c.execute('SELECT 1 FROM maas_dispatch_slots LIMIT 1').fetchone()
                zero = (budget['global']['total'] == 0 and bool(budget['pools'])
                        and all(p['reason'] == 'no_ready_replica' for p in budget['pools'].values()))
                return state['generation'], state['paused'] and idle, zero
            return state['generation'], state['managed_requests_drained'], False

        with self.service.db.transaction() as c:
            generation, drained, zero = resume_state(c)
        if not drained:
            raise ServiceError('MAINTENANCE_NOT_DRAINED','Pause and resolve all managed activity before resuming',409)
        if not zero:
            probe()
        with self.service.db.transaction() as c:
            current, drained, still_zero = resume_state(c)
            if current != generation or not drained or still_zero != zero:
                raise ServiceError('MAINTENANCE_CHANGED','Maintenance state changed during recovery probe',409)
            row=c.execute("UPDATE maintenance SET paused=false,generation=generation+1,updated_at=now() WHERE id=1 RETURNING generation").fetchone()
            reason = 'Fresh GPUStack budget has zero dispatch capacity' if zero else 'Registered model inference probe passed'
            c.execute("INSERT INTO maintenance_events(generation,action,reason) VALUES(%s,'verified-resume',%s)",(row['generation'],reason))
        return self.status()

    def resolve_sync(self,token,reason):
        if len(reason.strip())<10:raise ValueError('Provide backend termination evidence (at least 10 characters)')
        with self.service.db.transaction() as c:
            c.execute('SELECT id FROM maintenance WHERE id=1 FOR UPDATE')
            self.service.expire_sync(c)
            return bool(c.execute("UPDATE sync_activity SET state='RESOLVED',resolution_reason=%s,updated_at=now() WHERE token=%s AND state='UNKNOWN' RETURNING token",(reason,token)).fetchone())
