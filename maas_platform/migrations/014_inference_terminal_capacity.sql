-- Keep the legacy limits on active tasks while exposing remote completion to dynamic admission.
CREATE OR REPLACE VIEW inference_slots AS
SELECT t.model, t.project, true AS asynchronous, (b.token IS NOT NULL) AS dispatched,
       (b.state IN ('COMPLETED','RESOLVED')) IS TRUE AS backend_finished
FROM tasks t LEFT JOIN backend_activity b ON b.token=t.token
WHERE t.state IN ('RUNNING','CANCEL_REQUESTED')
UNION ALL
SELECT model, project, false, true, false FROM sync_activity WHERE state IN ('DISPATCHING','UNKNOWN')
UNION ALL
SELECT t.model, t.project, true, true, false FROM backend_activity b JOIN tasks t ON t.id=b.task_id
WHERE b.state='UNKNOWN' AND NOT (t.state IN ('RUNNING','CANCEL_REQUESTED') AND t.token IS NOT DISTINCT FROM b.token);
