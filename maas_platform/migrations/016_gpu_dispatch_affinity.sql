ALTER TABLE backend_activity ADD COLUMN dispatch_gpu_uuid text;
ALTER TABLE sync_activity ADD COLUMN dispatch_gpu_uuid text;

CREATE OR REPLACE VIEW inference_slots AS
SELECT t.model, t.project, true AS asynchronous, (b.token IS NOT NULL) AS dispatched,
       (b.state IN ('COMPLETED','RESOLVED')) IS TRUE AS backend_finished, b.dispatch_gpu_uuid, b.dispatch_pool_id
FROM tasks t LEFT JOIN backend_activity b ON b.token=t.token
WHERE t.state IN ('RUNNING','CANCEL_REQUESTED')
UNION ALL
SELECT model, project, false, true, gpu_finished_at IS NOT NULL, dispatch_gpu_uuid, dispatch_pool_id
FROM sync_activity WHERE state IN ('DISPATCHING','UNKNOWN')
UNION ALL
SELECT t.model, t.project, true, true, false, b.dispatch_gpu_uuid, b.dispatch_pool_id
FROM backend_activity b JOIN tasks t ON t.id=b.task_id
WHERE b.state='UNKNOWN' AND NOT (t.state IN ('RUNNING','CANCEL_REQUESTED') AND t.token IS NOT DISTINCT FROM b.token);
