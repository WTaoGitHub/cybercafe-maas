-- Bounded TTL selection must not sort the complete artifact history or scan
-- completed task history for each candidate. No new business state is added.
CREATE INDEX artifacts_live_expiry ON artifacts(expires_at,id) WHERE deleted_at IS NULL;
CREATE INDEX tasks_active_input ON tasks(input_artifact_id)
    WHERE input_artifact_id IS NOT NULL AND state IN ('QUEUED','RUNNING','RETRY_WAIT','CANCEL_REQUESTED');
