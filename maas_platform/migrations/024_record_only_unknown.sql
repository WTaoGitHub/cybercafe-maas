-- Keep the enforced contract across missing or expired observer snapshots.
ALTER TABLE maintenance ADD COLUMN record_unknown_only boolean NOT NULL DEFAULT false;
UPDATE maintenance m SET record_unknown_only = true
FROM capacity_snapshot s
WHERE m.id = 1 AND s.id = 1 AND m.capacity_policy_hash = s.policy_hash
  AND s.budget->>'source' = 'gpustack_ready';

-- All writers (including late replies and older maintenance tools) share this fence.
CREATE FUNCTION preserve_unknown_record() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF OLD.gpustack_routed OR (SELECT record_unknown_only FROM maintenance WHERE id = 1) THEN
        RETURN NULL;
    END IF;
    RETURN NEW;
END;
$$;
CREATE TRIGGER preserve_unknown_record BEFORE UPDATE ON sync_activity
    FOR EACH ROW WHEN (OLD.state = 'UNKNOWN') EXECUTE FUNCTION preserve_unknown_record();
CREATE TRIGGER preserve_unknown_record BEFORE UPDATE ON backend_activity
    FOR EACH ROW WHEN (OLD.state = 'UNKNOWN') EXECUTE FUNCTION preserve_unknown_record();

CREATE OR REPLACE VIEW boot_observation_pending AS
SELECT 'sync'::text kind, token,runtime_instance,gpu_instance,gpu_uuid,dispatch_route,boot_observation_session,unknown_since
FROM sync_activity
WHERE state='UNKNOWN' AND execution_guard AND NOT gpustack_routed AND NOT guard_conflict
  AND (guard_scope IS NOT NULL OR dispatch_gpu_uuid=gpu_uuid) AND gpu_finished_at IS NULL
  AND NOT (SELECT record_unknown_only FROM maintenance WHERE id=1)
UNION ALL
SELECT 'task',token,runtime_instance,gpu_instance,gpu_uuid,dispatch_route,boot_observation_session,unknown_since
FROM backend_activity
WHERE state='UNKNOWN' AND execution_guard AND NOT gpustack_routed AND NOT guard_conflict
  AND (guard_scope IS NOT NULL OR dispatch_gpu_uuid=gpu_uuid) AND gpu_finished_at IS NULL
  AND NOT (SELECT record_unknown_only FROM maintenance WHERE id=1);
