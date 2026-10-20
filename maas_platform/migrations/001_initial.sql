CREATE TABLE maintenance (
 id integer PRIMARY KEY CHECK (id=1), paused boolean NOT NULL DEFAULT false,
 reason text NOT NULL DEFAULT '', updated_at timestamptz NOT NULL DEFAULT now()
);
INSERT INTO maintenance(id) VALUES(1);
CREATE TABLE artifacts (
 id text PRIMARY KEY, project text NOT NULL, kind text NOT NULL CHECK(kind IN ('input','result')),
 media_type text NOT NULL, size bigint NOT NULL CHECK(size>=0), sha256 text NOT NULL,
 created_at timestamptz NOT NULL DEFAULT now(), expires_at timestamptz NOT NULL, deleted_at timestamptz
);
CREATE TABLE tasks (
 id text PRIMARY KEY, project text NOT NULL, credential_id text NOT NULL,
 idempotency_key text NOT NULL, request_hash text NOT NULL, model text NOT NULL,
 revision text NOT NULL, model_fingerprint text NOT NULL, input jsonb NOT NULL,
 parameters jsonb NOT NULL DEFAULT '{}', input_artifact_id text REFERENCES artifacts(id),
 state text NOT NULL CHECK(state IN ('QUEUED','RUNNING','RETRY_WAIT','CANCEL_REQUESTED','CANCELLED','SUCCEEDED','FAILED')),
 attempts integer NOT NULL DEFAULT 0, max_attempts integer NOT NULL,
 deadline timestamptz NOT NULL, next_run timestamptz NOT NULL DEFAULT now(),
 lease_until timestamptz, token text, result_artifact_id text REFERENCES artifacts(id),
 error_code text, created_at timestamptz NOT NULL DEFAULT now(), updated_at timestamptz NOT NULL DEFAULT now(),
 UNIQUE(project,idempotency_key)
);
CREATE INDEX tasks_queue_idx ON tasks(state,next_run,created_at);
CREATE INDEX tasks_project_idx ON tasks(project,state);
CREATE TABLE task_attempts (
 token text PRIMARY KEY, task_id text NOT NULL REFERENCES tasks(id), attempt integer NOT NULL,
 state text NOT NULL, started_at timestamptz NOT NULL DEFAULT now(), ended_at timestamptz, error_code text,
 UNIQUE(task_id,attempt)
);
CREATE TABLE task_usage_events (
 task_id text PRIMARY KEY REFERENCES tasks(id), project text NOT NULL,
 unit text NOT NULL DEFAULT 'successful_task', quantity integer NOT NULL CHECK(quantity=1),
 created_at timestamptz NOT NULL DEFAULT now()
);
