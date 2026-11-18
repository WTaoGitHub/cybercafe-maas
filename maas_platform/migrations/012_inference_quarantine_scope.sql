-- NULL means the dispatch predates scoped evidence. Empty route identifies local work.
ALTER TABLE backend_activity ADD COLUMN dispatch_route text, ADD COLUMN dispatch_pool_id bigint;
ALTER TABLE sync_activity ADD COLUMN dispatch_route text, ADD COLUMN dispatch_pool_id bigint;

-- An unknown execution still occupies a shared slot. Count an active task only once.
CREATE VIEW inference_slots AS
SELECT t.model, t.project, true AS asynchronous, (b.token IS NOT NULL) AS dispatched
FROM tasks t LEFT JOIN backend_activity b ON b.token=t.token
WHERE t.state IN ('RUNNING','CANCEL_REQUESTED')
UNION ALL
SELECT model, project, false, true FROM sync_activity WHERE state IN ('DISPATCHING','UNKNOWN')
UNION ALL
SELECT t.model, t.project, true, true FROM backend_activity b JOIN tasks t ON t.id=b.task_id
WHERE b.state='UNKNOWN' AND NOT (t.state IN ('RUNNING','CANCEL_REQUESTED') AND t.token IS NOT DISTINCT FROM b.token);
