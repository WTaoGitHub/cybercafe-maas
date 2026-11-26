-- Observation has no settlement, admission or usage writes.
CREATE TABLE boot_observation_source (
    cluster_id bigint NOT NULL, gpu_uuid text NOT NULL, seq bigint NOT NULL,
    worker_id bigint NOT NULL, session_id uuid NOT NULL, boot_id uuid,
    eligible boolean NOT NULL, nonce text, due timestamptz, proof text,
    PRIMARY KEY(cluster_id,gpu_uuid)
);
CREATE TABLE boot_session_history (
    cluster_id bigint NOT NULL, gpu_uuid text NOT NULL, seq bigint NOT NULL,
    worker_id bigint NOT NULL, session_id uuid NOT NULL, boot_id uuid NOT NULL,
    generation bigint NOT NULL, nonce text NOT NULL, proof text NOT NULL,
    confirmed_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    PRIMARY KEY(cluster_id,gpu_uuid,seq), UNIQUE(session_id)
);
CREATE INDEX boot_history_generation ON boot_session_history(cluster_id,gpu_uuid,generation);
ALTER TABLE sync_activity ADD COLUMN boot_observation_session uuid REFERENCES boot_session_history(session_id);
ALTER TABLE backend_activity ADD COLUMN boot_observation_session uuid REFERENCES boot_session_history(session_id);
CREATE TABLE boot_unknown_candidates (
    kind text NOT NULL, token text NOT NULL, cluster_id bigint NOT NULL, gpu_uuid text NOT NULL,
    old_seq bigint NOT NULL, new_seq bigint NOT NULL, unknown_since timestamptz NOT NULL,
    observed_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    PRIMARY KEY(kind,token),
    FOREIGN KEY(cluster_id,gpu_uuid,old_seq) REFERENCES boot_session_history,
    FOREIGN KEY(cluster_id,gpu_uuid,new_seq) REFERENCES boot_session_history
);
CREATE VIEW boot_observation_pending AS
SELECT 'sync'::text kind, token,runtime_instance,gpu_instance,gpu_uuid,dispatch_route,boot_observation_session,unknown_since
FROM sync_activity WHERE state='UNKNOWN' AND execution_guard AND NOT guard_conflict
  AND (guard_scope IS NOT NULL OR dispatch_gpu_uuid=gpu_uuid) AND gpu_finished_at IS NULL
UNION ALL SELECT 'task',token,runtime_instance,gpu_instance,gpu_uuid,dispatch_route,boot_observation_session,unknown_since
FROM backend_activity WHERE state='UNKNOWN' AND execution_guard AND NOT guard_conflict
  AND (guard_scope IS NOT NULL OR dispatch_gpu_uuid=gpu_uuid) AND gpu_finished_at IS NULL;
CREATE VIEW current_boot_unknown_candidates AS
SELECT p.kind,p.token,o.cluster_id,o.gpu_uuid,o.seq old_seq,n.seq new_seq,p.unknown_since
FROM boot_observation_pending p JOIN boot_session_history o ON o.session_id=p.boot_observation_session
JOIN execution_runtime r ON r.instance=p.runtime_instance AND r.instance=p.gpu_instance
  AND r.gpu_uuid=o.gpu_uuid AND r.gpu_uuid=p.gpu_uuid AND r.route=p.dispatch_route AND r.signer_boot=o.boot_id::text
JOIN LATERAL (SELECT h.seq FROM boot_session_history h WHERE h.cluster_id=o.cluster_id
  AND h.gpu_uuid=o.gpu_uuid AND h.generation=o.generation+1 ORDER BY h.seq LIMIT 1) n ON true;

-- The periodic reconciliation also covers a transition concurrent with confirmation.
CREATE FUNCTION note_boot_unknown() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    INSERT INTO boot_unknown_candidates(kind,token,cluster_id,gpu_uuid,old_seq,new_seq,unknown_since)
    SELECT * FROM current_boot_unknown_candidates WHERE kind=TG_ARGV[0] AND token=NEW.token
    ON CONFLICT DO NOTHING;
    RETURN NEW;
END;
$$;
CREATE TRIGGER boot_note_sync AFTER UPDATE OF state ON sync_activity
    FOR EACH ROW WHEN (NEW.state='UNKNOWN' AND NEW.boot_observation_session IS NOT NULL)
    EXECUTE FUNCTION note_boot_unknown('sync');
CREATE TRIGGER boot_note_backend AFTER UPDATE OF state ON backend_activity
    FOR EACH ROW WHEN (NEW.state='UNKNOWN' AND NEW.boot_observation_session IS NOT NULL)
    EXECUTE FUNCTION note_boot_unknown('task');
