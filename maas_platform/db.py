from contextlib import contextmanager
from pathlib import Path
import hashlib
import psycopg
from psycopg.rows import dict_row


class Database:
    def __init__(self, url):
        self.url = url

    @contextmanager
    def transaction(self):
        with psycopg.connect(self.url, row_factory=dict_row, connect_timeout=5) as conn:
            conn.execute("SET LOCAL statement_timeout = '15s'")
            conn.execute("SET LOCAL lock_timeout = '5s'")
            yield conn

    def migrate(self):
        with self.transaction() as conn:
            conn.execute('SELECT pg_advisory_xact_lock(842125223)')
            conn.execute('CREATE TABLE IF NOT EXISTS maas_migrations (name text PRIMARY KEY, sha256 text NOT NULL, applied_at timestamptz NOT NULL DEFAULT now())')
            for path in sorted((Path(__file__).parent / 'migrations').glob('[0-9]*.sql')):
                text = path.read_text(); digest = hashlib.sha256(text.encode()).hexdigest()
                row = conn.execute('SELECT sha256 FROM maas_migrations WHERE name=%s', (path.name,)).fetchone()
                if row:
                    if row['sha256'] != digest:
                        raise RuntimeError('Applied migration checksum mismatch')
                    continue
                conn.execute(text)
                conn.execute('INSERT INTO maas_migrations(name,sha256) VALUES(%s,%s)', (path.name, digest))
