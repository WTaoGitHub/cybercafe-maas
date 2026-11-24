-- Share immutable process sets across attempts on the same topology.
CREATE TABLE execution_scopes (
    id text PRIMARY KEY, processes jsonb NOT NULL CHECK (jsonb_typeof(processes)='object' AND processes <> '{}'::jsonb),
    topology jsonb NOT NULL, created_at timestamptz NOT NULL DEFAULT now()
);

-- Existing dispatches have no evidence of a mandatory guard at send time.
ALTER TABLE backend_activity ADD COLUMN guard_scope text REFERENCES execution_scopes(id),
    ADD COLUMN isolated_gpu_uuid text, ADD COLUMN isolation_evidence text,
    ADD COLUMN guard_conflict boolean NOT NULL DEFAULT false;
ALTER TABLE sync_activity ADD COLUMN guard_scope text REFERENCES execution_scopes(id),
    ADD COLUMN isolated_gpu_uuid text, ADD COLUMN isolation_evidence text,
    ADD COLUMN guard_conflict boolean NOT NULL DEFAULT false;

-- All paths which lose the transport or expire the lease make the same decision.
-- The row update serializes with the authoritative start grant.
CREATE FUNCTION fence_unstarted_execution() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF NEW.state = 'UNKNOWN' AND OLD.state = 'DISPATCHING'
       AND OLD.execution_guard AND OLD.guard_scope IS NOT NULL AND NOT NEW.guard_conflict
       AND OLD.gpu_uuid IS NULL AND OLD.gpu_instance IS NULL THEN
        NEW.state := 'RESOLVED';
        NEW.resolution_reason := 'Execution closed before any GPU grant';
    END IF;
    RETURN NEW;
END;
$$;
CREATE TRIGGER fence_unstarted_backend BEFORE UPDATE OF state ON backend_activity
    FOR EACH ROW EXECUTE FUNCTION fence_unstarted_execution();
CREATE TRIGGER fence_unstarted_sync BEFORE UPDATE OF state ON sync_activity
    FOR EACH ROW EXECUTE FUNCTION fence_unstarted_execution();

CREATE OR REPLACE VIEW inference_slots AS
SELECT t.model,t.project,true AS asynchronous,(b.token IS NOT NULL) AS dispatched,
       (b.state IN ('COMPLETED','RESOLVED')) IS TRUE AS backend_finished,b.dispatch_gpu_uuid,b.dispatch_pool_id,
       CASE WHEN b.state='UNKNOWN' THEN coalesce(b.isolated_gpu_uuid,
           CASE WHEN b.guard_scope IS NOT NULL THEN b.gpu_uuid END) END AS isolated_gpu_uuid
FROM tasks t LEFT JOIN backend_activity b ON b.token=t.token WHERE t.state IN ('RUNNING','CANCEL_REQUESTED')
UNION ALL
SELECT model,project,false,true,gpu_finished_at IS NOT NULL,dispatch_gpu_uuid,dispatch_pool_id,
       CASE WHEN state='UNKNOWN' THEN coalesce(isolated_gpu_uuid,
           CASE WHEN guard_scope IS NOT NULL THEN gpu_uuid END) END
FROM sync_activity WHERE state IN ('DISPATCHING','UNKNOWN')
UNION ALL
SELECT t.model,t.project,true,true,false,b.dispatch_gpu_uuid,b.dispatch_pool_id,
       coalesce(b.isolated_gpu_uuid,CASE WHEN b.guard_scope IS NOT NULL THEN b.gpu_uuid END)
FROM backend_activity b JOIN tasks t ON t.id=b.task_id
WHERE b.state='UNKNOWN' AND NOT (t.state IN ('RUNNING','CANCEL_REQUESTED') AND t.token IS NOT DISTINCT FROM b.token);
