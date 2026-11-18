CREATE TABLE task_submission_freezes (model text PRIMARY KEY);

-- Reserve the original project's namespace without importing its tasks or files.
CREATE TABLE task_external_keys (
    project text NOT NULL,
    key_hash text NOT NULL CHECK (key_hash ~ '^[0-9a-f]{64}$'),
    task_id text NOT NULL,
    PRIMARY KEY (project, key_hash)
);
