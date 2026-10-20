-- A task terminal state is not evidence that remote GPU execution stopped.
CREATE TABLE backend_activity (
 token text PRIMARY KEY REFERENCES task_attempts(token),
 task_id text NOT NULL REFERENCES tasks(id),
 state text NOT NULL CHECK(state IN ('DISPATCHING','UNKNOWN','COMPLETED','RESOLVED')),
 created_at timestamptz NOT NULL DEFAULT now(), updated_at timestamptz NOT NULL DEFAULT now(),
 resolution_reason text
);
CREATE INDEX backend_activity_unresolved_idx ON backend_activity(state);
