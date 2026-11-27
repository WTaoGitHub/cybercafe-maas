-- Persist the contract of this attempt across policy changes and delayed callbacks.
ALTER TABLE sync_activity ADD COLUMN gpustack_routed boolean NOT NULL DEFAULT false;
ALTER TABLE backend_activity ADD COLUMN gpustack_routed boolean NOT NULL DEFAULT false;

CREATE VIEW maas_dispatch_slots AS
SELECT model, project, false AS asynchronous, true AS dispatched
FROM sync_activity WHERE state='DISPATCHING'
UNION ALL
SELECT t.model, t.project, true, b.token IS NOT NULL
FROM tasks t LEFT JOIN backend_activity b ON b.token=t.token
WHERE t.state IN ('RUNNING','CANCEL_REQUESTED')
  AND (b.token IS NULL OR b.state IN ('DISPATCHING','COMPLETED'));

CREATE OR REPLACE VIEW boot_observation_pending AS
SELECT 'sync'::text kind, token,runtime_instance,gpu_instance,gpu_uuid,dispatch_route,boot_observation_session,unknown_since
FROM sync_activity WHERE state='UNKNOWN' AND execution_guard AND NOT guard_conflict
  AND (gpustack_routed OR guard_scope IS NOT NULL OR dispatch_gpu_uuid=gpu_uuid) AND gpu_finished_at IS NULL
UNION ALL SELECT 'task',token,runtime_instance,gpu_instance,gpu_uuid,dispatch_route,boot_observation_session,unknown_since
FROM backend_activity WHERE state='UNKNOWN' AND execution_guard AND NOT guard_conflict
  AND (gpustack_routed OR guard_scope IS NOT NULL OR dispatch_gpu_uuid=gpu_uuid) AND gpu_finished_at IS NULL;
