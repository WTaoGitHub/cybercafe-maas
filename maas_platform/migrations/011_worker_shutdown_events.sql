CREATE TABLE worker_shutdown_events (
    event_id uuid PRIMARY KEY,
    event_type text NOT NULL CHECK (event_type = 'worker.shutdown.requested'),
    cluster_id integer NOT NULL CHECK (cluster_id > 0),
    worker_id bigint NOT NULL CHECK (worker_id > 0),
    gpu_uuid text NOT NULL CHECK (gpu_uuid ~ '^GPU-[0-9a-f]{8}(-[0-9a-f]{4}){3}-[0-9a-f]{12}$'),
    boot_id uuid NOT NULL,
    session_id uuid NOT NULL,
    occurred_at timestamptz NOT NULL,
    received_at timestamptz NOT NULL,
    expires_at timestamptz NOT NULL CHECK (expires_at = received_at + interval '48 hours')
);
CREATE INDEX worker_shutdown_events_expiry ON worker_shutdown_events (expires_at, event_id);
