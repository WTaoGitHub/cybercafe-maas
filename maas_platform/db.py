from contextlib import contextmanager
from pathlib import Path
import hashlib, re
import psycopg
from psycopg.rows import dict_row


def migration_files():
    # glob() can silently return an empty list on an unreadable directory.
    # Refuse a healthy-looking startup without packaged SQL migrations.
    directory=Path(__file__).parent / 'migrations'
    files=sorted(p for p in directory.iterdir() if p.is_file() and re.fullmatch(r'[0-9].*\.sql',p.name))
    if not files:raise RuntimeError('No database migration files available')
    return files


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
        paths=migration_files()
        with self.transaction() as conn:
            conn.execute('SELECT pg_advisory_xact_lock(842125223)')
            conn.execute('CREATE TABLE IF NOT EXISTS maas_migrations (name text PRIMARY KEY, sha256 text NOT NULL, applied_at timestamptz NOT NULL DEFAULT now())')
            for path in paths:
                text = path.read_text(); digest = hashlib.sha256(text.encode()).hexdigest()
                row = conn.execute('SELECT sha256 FROM maas_migrations WHERE name=%s', (path.name,)).fetchone()
                if row:
                    if row['sha256'] != digest:
                        raise RuntimeError('Applied migration checksum mismatch')
                    continue
                conn.execute(text)
                conn.execute('INSERT INTO maas_migrations(name,sha256) VALUES(%s,%s)', (path.name, digest))
