-- Runtime identity is optional for older workers and immutable for each guarded process.
CREATE TABLE execution_runtime (
    instance text PRIMARY KEY,
    gpu_uuid text NOT NULL,
    route text NOT NULL,
    identity jsonb NOT NULL,
    registered_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    last_checked_at timestamptz,
    last_outcome text,
    next_check_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    challenge text,
    challenge_until timestamptz,
    attempts integer NOT NULL DEFAULT 0,
    terminal_proof jsonb
);
CREATE INDEX execution_runtime_checks ON execution_runtime (gpu_uuid, next_check_at);

ALTER TABLE backend_activity ADD COLUMN runtime_instance text REFERENCES execution_runtime(instance),
    ADD COLUMN unknown_since timestamptz;
ALTER TABLE sync_activity ADD COLUMN runtime_instance text REFERENCES execution_runtime(instance),
    ADD COLUMN unknown_since timestamptz;

UPDATE backend_activity SET unknown_since=updated_at WHERE state='UNKNOWN';
UPDATE sync_activity SET unknown_since=updated_at WHERE state='UNKNOWN';

CREATE FUNCTION set_unknown_since() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF NEW.state = 'UNKNOWN' AND (TG_OP = 'INSERT' OR OLD.state <> 'UNKNOWN') THEN
        NEW.unknown_since := clock_timestamp();
    END IF;
    RETURN NEW;
END;
$$;

-- Trigger names order this AFTER the guard's BEFORE trigger.
CREATE TRIGGER witness_backend_unknown BEFORE INSERT OR UPDATE OF state ON backend_activity
    FOR EACH ROW EXECUTE FUNCTION set_unknown_since();
CREATE TRIGGER witness_sync_unknown BEFORE INSERT OR UPDATE OF state ON sync_activity
    FOR EACH ROW EXECUTE FUNCTION set_unknown_since();

CREATE INDEX backend_runtime_unknown ON backend_activity (runtime_instance, unknown_since) WHERE state='UNKNOWN';
CREATE INDEX sync_runtime_unknown ON sync_activity (runtime_instance, unknown_since) WHERE state='UNKNOWN';
