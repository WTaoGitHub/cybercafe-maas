ALTER TABLE sync_activity ADD COLUMN is_probe boolean NOT NULL DEFAULT false;
ALTER TABLE sync_activity ADD COLUMN outcome text;
ALTER TABLE sync_activity ADD COLUMN finished_at timestamptz;
CREATE TABLE sync_usage_events (
    token text PRIMARY KEY REFERENCES sync_activity(token),
    project text NOT NULL,
    model text NOT NULL,
    unit text NOT NULL DEFAULT 'successful_request',
    quantity integer NOT NULL DEFAULT 1 CHECK (quantity=1),
    created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX artifacts_live_project ON artifacts(project) WHERE deleted_at IS NULL;
