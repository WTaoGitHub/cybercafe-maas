-- Independent from artifact TTL; no backfill of historical success events.
CREATE TABLE ocr_usage_records (
 id text PRIMARY KEY,
 project text NOT NULL,
 source_type text NOT NULL CHECK(source_type IN ('task','sync')),
 source_id text NOT NULL,
 receipt jsonb NOT NULL,
 created_at timestamptz NOT NULL DEFAULT now(),
 UNIQUE(source_type,source_id)
);
