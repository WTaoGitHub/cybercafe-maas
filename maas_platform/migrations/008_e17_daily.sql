CREATE TABLE e17.daily_observations (
    at bigint PRIMARY KEY,
    session_id uuid NOT NULL,
    cards jsonb NOT NULL
);

CREATE TABLE e17.daily_reports (
    day date PRIMARY KEY,
    payload jsonb NOT NULL,
    delivered_at timestamptz,
    attempts integer NOT NULL DEFAULT 0,
    next_attempt timestamptz NOT NULL DEFAULT now()
);
