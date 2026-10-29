"""Incremental GC on the existing shared POSIX volume, with no scheduling DB.

The scan cursor belongs to one process. Leadership failover starts a new scan;
it never authorizes deletion without the ordinary lifecycle lock and DB check.
Time limits are cooperative, not hard wall-clock guarantees for filesystem I/O.
"""
from collections import deque
import fcntl
import logging
import os
import re
import stat
import threading
import time

from .artifacts import lifecycle_lock

log = logging.getLogger('maas.artifact_gc')
OWNED_FILE = re.compile(r'\.?art_[0-9a-f]{32}')


def query_budget(connection, milliseconds):
    connection.execute("SELECT set_config('statement_timeout',%s,true),set_config('lock_timeout',%s,true)",
                       (str(milliseconds)+'ms',str(milliseconds)+'ms'))


class ArtifactGC:
    def __init__(self, service):
        self.service = service
        self.iterator = None
        self.pending = deque()
        self.scan_lock = threading.Lock()
        self.last_stats = {}

    def close(self):
        with self.scan_lock:
            if self.iterator is not None:
                self.iterator.close()
                self.iterator = None
            self.pending.clear()

    def _batch(self, deadline, stats):
        names = []
        if self.iterator is None and not self.pending:
            self.iterator = os.scandir(self.service.store.root)
        while stats['visited'] < self.service.settings.cleanup_batch_size and time.monotonic() < deadline:
            if self.pending:
                name = self.pending.popleft()
            elif self.iterator is not None:
                try:
                    name = next(self.iterator).name
                except StopIteration:
                    self.iterator.close()
                    self.iterator = None
                    stats['end_of_scan'] = True
                    break
            else:
                break
            stats['visited'] += 1  # Count all entries, not just deleted files.
            if OWNED_FILE.fullmatch(name):
                names.append(name)
        return names

    def orphans(self, *, blocking=True):
        s = self.service
        stats = {'visited':0,'removed':0,'known':0,'busy':False,'end_of_scan':False}
        started = time.monotonic()
        deadline = started + s.settings.cleanup_time_budget_ms / 1000
        cutoff = time.time() - s.settings.orphan_grace_seconds
        # This mutex only serializes GC cursor access, never upload/finish.
        with self.scan_lock:
            names = self._batch(deadline,stats)
            self.pending.extendleft(reversed(names))  # Retain work on any exception.
            if names:
                # Known live metadata can be skipped safely without the file lock.
                with s.db.transaction() as c:
                    query_budget(c,s.settings.cleanup_time_budget_ms)
                    known = {r['id'] for r in c.execute(
                        'SELECT id FROM artifacts WHERE id=ANY(%s) AND deleted_at IS NULL',
                        (list({name.lstrip('.') for name in names}),)).fetchall()}
                stats['known'] = sum(name.lstrip('.') in known for name in names)
                for name in names:
                    if name.lstrip('.') in known:
                        self.pending.popleft()
                        continue
                    if time.monotonic() >= deadline:
                        break
                    with lifecycle_lock(s.store,blocking=blocking) as acquired:
                        if not acquired:
                            stats['busy'] = True
                            break
                        path = s.store.root / name
                        try:
                            info = path.lstat()
                        except FileNotFoundError:
                            self.pending.popleft()
                            continue
                        if stat.S_ISREG(info.st_mode) and info.st_mtime <= cutoff:
                            # Upload/finish may have committed since the bulk read.
                            # All writers hold this lock through metadata commit.
                            with s.db.transaction() as c:
                                query_budget(c,s.settings.cleanup_time_budget_ms)
                                live = c.execute('SELECT 1 FROM artifacts WHERE id=%s AND deleted_at IS NULL',
                                                 (name.lstrip('.'),)).fetchone()
                                if not live:
                                    path.unlink()
                                    stats['removed'] += 1
                    self.pending.popleft()
            stats['pending'] = len(self.pending)
        stats['seconds'] = time.monotonic() - started
        self.last_stats = stats
        return stats['removed']

    def expired(self, *, blocking=True):
        s = self.service
        # Keep the file deletion and metadata transaction under the writer lock.
        # This pass already has a DB row bound; also cap its loop and SQL waits.
        with lifecycle_lock(s.store,blocking=blocking) as acquired:
            if not acquired:
                return 0
            deadline = time.monotonic() + s.settings.cleanup_time_budget_ms / 1000
            with s.db.transaction() as c:
                query_budget(c,s.settings.cleanup_time_budget_ms)
                rows = c.execute("""SELECT * FROM artifacts a WHERE a.expires_at<=now() AND a.deleted_at IS NULL
                    AND NOT EXISTS (SELECT 1 FROM tasks t WHERE t.input_artifact_id=a.id
                        AND t.state IN ('QUEUED','RUNNING','RETRY_WAIT','CANCEL_REQUESTED'))
                    ORDER BY a.expires_at LIMIT %s FOR UPDATE OF a SKIP LOCKED""",
                    (s.settings.cleanup_batch_size,)).fetchall()
                removed = 0
                for row in rows:
                    if time.monotonic() >= deadline:
                        break
                    s.store.remove(row['id'])
                    c.execute('UPDATE artifacts SET deleted_at=now() WHERE id=%s',(row['id'],))
                    removed += 1
                return removed


class PeriodicArtifactCleanup:
    """One elected executor per artifact volume; flock death releases leadership.

    Other executors never wait for this lock. The leader runs bounded passes
    between tasks; this is best effort maintenance, not a retention deadline SLA.
    """
    def __init__(self, service):
        self.service = service
        self.leader = None
        self.next_run = 0

    def tick(self):
        now = time.monotonic()
        if now < self.next_run:
            return False
        self.next_run = now + self.service.settings.cleanup_interval_seconds
        if self.leader is None:
            lock = (self.service.store.root / '.gc-coordinator.lock').open('a+b')
            os.chmod(lock.fileno(),0o600)
            try:
                fcntl.flock(lock,fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                lock.close()
                return False
            self.leader = lock
        expired = self.service.cleanup(blocking=False)
        orphans = self.service.cleanup_orphans(blocking=False)
        log.info('artifact_gc expired=%s orphans=%s stats=%s',expired,orphans,self.service.gc.last_stats)
        return True

    def close(self):
        self.service.gc.close()
        if self.leader is not None:
            self.leader.close()
            self.leader = None
