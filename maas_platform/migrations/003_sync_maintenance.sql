ALTER TABLE maintenance ADD COLUMN generation bigint NOT NULL DEFAULT 0;
CREATE TABLE sync_activity (
 token text PRIMARY KEY, project text NOT NULL, model text NOT NULL,
 state text NOT NULL CHECK(state IN ('DISPATCHING','UNKNOWN','COMPLETED','RESOLVED')),
 deadline timestamptz NOT NULL, created_at timestamptz NOT NULL DEFAULT now(),
 updated_at timestamptz NOT NULL DEFAULT now(), resolution_reason text
);
CREATE INDEX sync_activity_state_idx ON sync_activity(state);
CREATE TABLE maintenance_events (
 id bigserial PRIMARY KEY, generation bigint NOT NULL, action text NOT NULL,
 reason text NOT NULL, created_at timestamptz NOT NULL DEFAULT now()
);
