"""Disposable PostgreSQL and real filesystem/process locks; no inference."""
from contextlib import contextmanager
import hashlib
import multiprocessing as mp
import os
import threading
import time
from types import SimpleNamespace
from concurrent.futures import ThreadPoolExecutor

import pytest
from test_integration import env,submit
from maas_platform.artifact_gc import PeriodicArtifactCleanup
from maas_platform.artifacts import lifecycle_lock


def registered_files(service,count):
    records=[]
    old=time.time()-service.settings.orphan_grace_seconds-10
    for n in range(count):
        ident='art_'+format(n,'032x');path=service.store.path(ident)
        path.write_bytes(b'x');os.utime(path,(old,old))
        records.append((ident,hashlib.sha256(b'x').hexdigest()))
    with service.db.transaction() as c:
        with c.cursor() as cursor:
            cursor.executemany("""INSERT INTO artifacts(id,project,kind,media_type,size,sha256,expires_at)
                VALUES(%s,'a','input','text/plain',1,%s,now()+interval '1 day')""",records)
    return {r[0] for r in records}


def old_orphan(service):
    record=service.store.put(b'orphan');path=service.store.path(record['id'])
    old=time.time()-service.settings.orphan_grace_seconds-10
    os.utime(path,(old,old));return path


def test_known_files_scan_is_bounded_and_does_not_hold_writer_lock(env,monkeypatch):
    client,s,settings,auth,_=env;settings.cleanup_batch_size=200;settings.cleanup_time_budget_ms=1000
    known=registered_files(s,3000);queried=[];began=threading.Event();release=threading.Event()
    assert submit(client).status_code==202;task=s.claim();assert task
    transaction=s.db.transaction
    class Connection:
        def __init__(self,c):self.c=c
        def execute(self,sql,args=None):
            if sql.startswith('SELECT id FROM artifacts WHERE id=ANY'):
                queried.append(list(args[0]));began.set();assert release.wait(5)
            return self.c.execute(sql,args)
    @contextmanager
    def wrapped():
        with transaction() as c:yield Connection(c)
    monkeypatch.setattr(s.db,'transaction',wrapped)
    with ThreadPoolExecutor(2) as pool:
        gc=pool.submit(s.cleanup_orphans);assert began.wait(5)
        try:
            # This completes while the GC DB read is deliberately held open.
            upload=pool.submit(s.upload,auth.authenticate('gpustack_registered_a'),b'new','text/plain')
            record=upload.result(timeout=3)
            assert not gc.done() and s.store.path(record['id']).exists()
            finished=pool.submit(s.finish,task,output={'ok':True})
            assert finished.result(timeout=3) and not gc.done()
        finally:release.set()
        assert gc.result(timeout=5)==0
    assert len(queried)==1 and len(queried[0])<=200 and s.gc.last_stats['visited']<=200
    assert s.gc.last_stats['known']>0 and len(known)==3000


def test_cursor_passes_known_and_foreign_entries_and_keeps_young_files_and_links(env):
    _,s,settings,*_=env;settings.cleanup_batch_size=4;settings.cleanup_time_budget_ms=1000
    known=registered_files(s,17)
    for n in range(20):(s.store.root/('foreign-'+str(n))).write_text('keep')
    orphan=old_orphan(s);young=s.store.path(s.store.put(b'young')['id'])
    target=s.store.root/'outside-format';target.write_text('keep')
    link=s.store.root/('art_'+'f'*32);link.symlink_to(target)
    for _ in range(40):
        s.cleanup_orphans();assert s.gc.last_stats['visited']<=4
        if not orphan.exists():break
    assert not orphan.exists()
    assert all(s.store.path(i).exists() for i in known)
    assert young.exists() and target.read_text()=='keep' and link.is_symlink()
    assert len(list(s.store.root.glob('foreign-*')))==20


def test_busy_background_cleanup_keeps_pending_orphan(env):
    _,s,settings,*_=env;settings.cleanup_time_budget_ms=1000
    orphan=old_orphan(s)
    with lifecycle_lock(s.store):
        assert s.cleanup_orphans(blocking=False)==0
        assert s.gc.last_stats['busy'] and s.gc.last_stats['pending']>=1
        assert s.cleanup(blocking=False)==0
    assert s.cleanup_orphans(blocking=False)==1 and not orphan.exists()


def test_failed_bulk_read_does_not_lose_scanned_candidates(env,monkeypatch):
    _,s,settings,*_=env;settings.cleanup_time_budget_ms=1000
    orphan=old_orphan(s);transaction=s.db.transaction
    @contextmanager
    def unavailable():raise RuntimeError('test database unavailable');yield
    monkeypatch.setattr(s.db,'transaction',unavailable)
    with pytest.raises(RuntimeError):s.cleanup_orphans()
    assert orphan.exists() and len(s.gc.pending)>=1
    monkeypatch.setattr(s.db,'transaction',transaction)
    assert s.cleanup_orphans()==1 and not orphan.exists()


def test_expired_cleanup_remains_bounded_and_pins_active_input(env):
    client,s,settings,auth,_=env;settings.cleanup_batch_size=3;settings.cleanup_time_budget_ms=1000
    who=auth.authenticate('gpustack_registered_a')
    active=s.upload(who,b'active','text/plain')
    assert submit(client,input={'text':'held','artifact_id':active['id']}).status_code==202
    for _ in range(10):s.upload(who,b'expired','text/plain')
    with s.db.transaction() as c:c.execute("UPDATE artifacts SET expires_at=now()-interval '1 second'")
    removed=[]
    for _ in range(5):removed.append(s.cleanup())
    assert sum(removed)==10 and max(removed)<=3
    assert s.store.path(active['id']).read_bytes()==b'active'
    with s.db.transaction() as c:
        assert c.execute('SELECT count(*) n FROM artifacts WHERE deleted_at IS NULL').fetchone()['n']==1


def test_time_budget_preserves_unprocessed_candidates(env,monkeypatch):
    from pathlib import Path
    import maas_platform.artifact_gc as module
    _,s,settings,*_=env;settings.cleanup_batch_size=20;settings.cleanup_time_budget_ms=100
    orphans=[old_orphan(s) for _ in range(10)];clock=[0.0]
    # Simulate one slow deletion without a fragile wall-clock assertion.
    original=Path.unlink
    def unlink(path,*args,**kwargs):
        result=original(path,*args,**kwargs)
        if path in orphans:clock[0]+=.2
        return result
    monkeypatch.setattr(module,'time',SimpleNamespace(time=time.time,monotonic=lambda:clock[0]))
    monkeypatch.setattr(Path,'unlink',unlink)
    assert s.cleanup_orphans()==1 and len(s.gc.pending)>=9
    for _ in range(12):s.cleanup_orphans()
    assert all(not path.exists() for path in orphans)


def coordinator_child(root,connection):
    gc=SimpleNamespace(last_stats={},close=lambda:None)
    service=SimpleNamespace(store=SimpleNamespace(root=root),settings=SimpleNamespace(cleanup_interval_seconds=5),gc=gc,
        cleanup=lambda **_:0,cleanup_orphans=lambda **_:0)
    coordinator=PeriodicArtifactCleanup(service)
    try:
        while connection.poll(10):
            action=connection.recv()
            if action=='stop':break
            assert action=='tick';coordinator.next_run=0
            connection.send((os.getpid(),coordinator.tick()))
    finally:coordinator.close();connection.close()


def test_only_one_process_cleans_and_crash_releases_leadership(tmp_path):
    # A killed process can poison a multiprocessing Event's shared semaphore.
    # Independent pipes keep the crash test harness separate from GC fencing.
    ctx=mp.get_context('spawn');pipes=[ctx.Pipe() for _ in range(2)]
    procs=[ctx.Process(target=coordinator_child,args=(tmp_path,child)) for _,child in pipes]
    try:
        for p in procs:p.start()
        for parent,child in pipes:child.close();parent.send('tick')
        initial=[]
        for parent,_ in pipes:assert parent.poll(10);initial.append(parent.recv())
        assert sum(v for _,v in initial)==1
        leader=next(p for p in procs if (p.pid,True) in initial)
        leader.kill();leader.join(timeout=5);assert not leader.is_alive()
        follower=next(p for p in procs if p is not leader);parent=pipes[procs.index(follower)][0]
        parent.send('tick');assert parent.poll(10);pid,worked=parent.recv()
        assert worked and pid!=leader.pid
        parent.send('stop')
        for p in procs:
            if p is not leader:p.join(timeout=10);assert p.exitcode==0
    finally:
        for p in procs:
            if p.is_alive():p.kill();p.join(timeout=5)
        for parent,child in pipes:parent.close();child.close()
