-- PostgreSQL delivers NOTIFY after commit and coalesces equal payloads in a
-- transaction. Observers always reload the complete isolation set from the DB.
CREATE FUNCTION notify_gpu_quarantine() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
    previous_gpu text;
    current_gpu text;
BEGIN
    IF TG_OP <> 'INSERT' AND OLD.state = 'UNKNOWN' THEN
        previous_gpu := coalesce(OLD.isolated_gpu_uuid,
                                 CASE WHEN OLD.guard_scope IS NOT NULL THEN OLD.gpu_uuid END);
    END IF;
    IF TG_OP <> 'DELETE' AND NEW.state = 'UNKNOWN' THEN
        current_gpu := coalesce(NEW.isolated_gpu_uuid,
                                CASE WHEN NEW.guard_scope IS NOT NULL THEN NEW.gpu_uuid END);
    END IF;
    IF previous_gpu IS DISTINCT FROM current_gpu THEN
        PERFORM pg_notify('maas_gpu_quarantine', '');
    END IF;
    RETURN NULL;
END;
$$;

CREATE TRIGGER quarantine_sync_insert AFTER INSERT ON sync_activity
    FOR EACH ROW WHEN (NEW.state='UNKNOWN') EXECUTE FUNCTION notify_gpu_quarantine();
CREATE TRIGGER quarantine_sync_update AFTER UPDATE OF state, isolated_gpu_uuid, guard_scope, gpu_uuid ON sync_activity
    FOR EACH ROW WHEN (OLD.state='UNKNOWN' OR NEW.state='UNKNOWN') EXECUTE FUNCTION notify_gpu_quarantine();
CREATE TRIGGER quarantine_sync_delete AFTER DELETE ON sync_activity
    FOR EACH ROW WHEN (OLD.state='UNKNOWN') EXECUTE FUNCTION notify_gpu_quarantine();
CREATE TRIGGER quarantine_backend_insert AFTER INSERT ON backend_activity
    FOR EACH ROW WHEN (NEW.state='UNKNOWN') EXECUTE FUNCTION notify_gpu_quarantine();
CREATE TRIGGER quarantine_backend_update AFTER UPDATE OF state, isolated_gpu_uuid, guard_scope, gpu_uuid ON backend_activity
    FOR EACH ROW WHEN (OLD.state='UNKNOWN' OR NEW.state='UNKNOWN') EXECUTE FUNCTION notify_gpu_quarantine();
CREATE TRIGGER quarantine_backend_delete AFTER DELETE ON backend_activity
    FOR EACH ROW WHEN (OLD.state='UNKNOWN') EXECUTE FUNCTION notify_gpu_quarantine();
