CREATE SCHEMA e17;

CREATE TABLE e17.samples (
    at bigint PRIMARY KEY,
    session_id uuid NOT NULL,
    cards jsonb
);

CREATE TABLE e17.cards (
    uuid text PRIMARY KEY,
    first_seen bigint NOT NULL
);
