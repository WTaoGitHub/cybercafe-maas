CREATE TABLE capacity_snapshot (
    id integer PRIMARY KEY CHECK (id = 1),
    generation bigint NOT NULL DEFAULT 1,
    policy_hash text NOT NULL,
    observed_at timestamptz NOT NULL,
    expires_at timestamptz NOT NULL CHECK (expires_at > observed_at),
    budget jsonb NOT NULL
);
