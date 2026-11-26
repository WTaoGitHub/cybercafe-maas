-- A single-GPU dispatch has no shared-pool guard_scope. Its authenticated start
-- and host-bound runtime can independently identify the card for withdrawal.
CREATE FUNCTION isolate_bound_pinned_gpu() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
    source CONSTANT text := 'Original single-GPU dispatch matched host-bound execution grant';
BEGIN
    IF NEW.isolated_gpu_uuid IS NOT NULL AND NEW.isolation_evidence IS DISTINCT FROM source THEN
        RETURN NEW;  -- Preserve separately recorded operator evidence.
    END IF;
    IF NEW.guard_scope IS NULL AND NEW.execution_guard AND NOT NEW.guard_conflict
       AND NEW.gpu_finished_at IS NULL AND NEW.dispatch_gpu_uuid = NEW.gpu_uuid
       AND NEW.runtime_instance = NEW.gpu_instance AND EXISTS (
           SELECT 1 FROM execution_runtime r WHERE r.instance = NEW.runtime_instance
             AND r.gpu_uuid = NEW.gpu_uuid AND r.route = NEW.dispatch_route) THEN
        NEW.isolated_gpu_uuid := NEW.gpu_uuid;
        NEW.isolation_evidence := source;
    ELSIF NEW.isolation_evidence = source THEN
        NEW.isolated_gpu_uuid := NULL;
        NEW.isolation_evidence := NULL;
    END IF;
    RETURN NEW;
END;
$$;

CREATE TRIGGER isolate_pinned_sync BEFORE INSERT OR UPDATE ON sync_activity
    FOR EACH ROW WHEN (NEW.state='UNKNOWN') EXECUTE FUNCTION isolate_bound_pinned_gpu();
CREATE TRIGGER isolate_pinned_backend BEFORE INSERT OR UPDATE ON backend_activity
    FOR EACH ROW WHEN (NEW.state='UNKNOWN') EXECUTE FUNCTION isolate_bound_pinned_gpu();

-- Before triggers may change the effective card even when the UPDATE only
-- names guard_conflict. Notify on changes to the final row, after commit.
DROP TRIGGER quarantine_sync_update ON sync_activity;
CREATE TRIGGER quarantine_sync_update AFTER UPDATE ON sync_activity
    FOR EACH ROW WHEN (OLD.state='UNKNOWN' OR NEW.state='UNKNOWN') EXECUTE FUNCTION notify_gpu_quarantine();
DROP TRIGGER quarantine_backend_update ON backend_activity;
CREATE TRIGGER quarantine_backend_update AFTER UPDATE ON backend_activity
    FOR EACH ROW WHEN (OLD.state='UNKNOWN' OR NEW.state='UNKNOWN') EXECUTE FUNCTION notify_gpu_quarantine();

-- Re-evaluate outstanding pins without changing the original UNKNOWN timestamp.
UPDATE sync_activity SET state=state WHERE state='UNKNOWN' AND guard_scope IS NULL
    AND isolated_gpu_uuid IS NULL AND dispatch_gpu_uuid=gpu_uuid AND runtime_instance IS NOT NULL;
UPDATE backend_activity SET state=state WHERE state='UNKNOWN' AND guard_scope IS NULL
    AND isolated_gpu_uuid IS NULL AND dispatch_gpu_uuid=gpu_uuid AND runtime_instance IS NOT NULL;
