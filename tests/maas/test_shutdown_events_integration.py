"""Actual PostgreSQL storage/cleanup; never point this fixture at a live DB."""
import asyncio
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
import json
from threading import Event
from types import SimpleNamespace
from uuid import uuid4

from fastapi.testclient import TestClient
import psycopg
import pytest

from maas_bootstrap.shutdown_auth import issue
from maas_platform import api as api_module, shutdown_events as module
from test_integration import env

SCOPE = dict(cluster_id=1, worker_id=42, gpu_uuid='GPU-aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee',
             boot_id='f591ba40-e440-4ede-9659-9ad92cdeba2b',
             session_id='ee15cb4c-0886-4ad7-a7ef-3802965d340e')
CUTOFF = datetime(2026, 11, 18, tzinfo=timezone.utc)


@pytest.fixture(autouse=True)
def direct_cleanup_tests(monkeypatch):
    async def idle(db, stop):
        await stop.wait()
    monkeypatch.setattr(api_module, 'shutdown_cleanup', idle)


def payload(**changes):
    return dict(SCOPE, event_id=str(uuid4()), event_type='worker.shutdown.requested',
                occurred_at='2020-01-01T00:00:00Z') | changes


@pytest.fixture
def event_env(env, tmp_path, monkeypatch):
    _, service, settings, auth, _ = env
    key = tmp_path/'event-key'
    key.write_text(bytes(range(32)).hex());key.chmod(0o600)
    settings.shutdown_event_key_file = key
    monkeypatch.setattr(auth, '_request', lambda *a, **k: pytest.fail('Event endpoint called inference auth'))
    with TestClient(api_module.create_app(settings, auth), raise_server_exceptions=False) as client:
        yield client, service.db, key, settings, auth


def post(event_env, body=None, scope=None):
    client, _, key, *_ = event_env
    return client.post(module.PATH, json=body or payload(),
                       headers={'Authorization':'Bearer ' + issue(key, scope or SCOPE)})


def rows(db):
    with db.transaction() as conn:
        return conn.execute('SELECT * FROM worker_shutdown_events ORDER BY received_at,event_id').fetchall()


def test_commit_receipt_and_same_vpc_events_eight_hours_apart(event_env):
    _, db, *_ = event_env
    first = payload()
    response = post(event_env, first)
    assert response.status_code == 201 and response.headers['cache-control'] == 'no-store'
    assert set(response.json()) == {'event_id','received_at','expires_at'}
    with db.transaction() as conn:
        conn.execute("UPDATE worker_shutdown_events SET received_at=statement_timestamp()-interval '8 hours', expires_at=statement_timestamp()+interval '40 hours'")
    second = payload(occurred_at='2099-01-01T00:00:00+08:00')
    assert post(event_env, second).status_code == 201
    saved = rows(db)
    assert len(saved) == 2 and saved[0]['event_id'] != saved[1]['event_id']
    assert saved[0]['worker_id'] == saved[1]['worker_id'] == SCOPE['worker_id']
    assert timedelta(hours=8) <= saved[1]['received_at']-saved[0]['received_at'] < timedelta(hours=8, seconds=2)
    assert all(row['expires_at']-row['received_at'] == timedelta(hours=48) for row in saved)
    old = saved[0]
    assert post(event_env, first).status_code == 409
    assert rows(db)[0] == old


@pytest.mark.parametrize('change', [{'worker_id':43}, {'cluster_id':2},
    {'gpu_uuid':'GPU-bbbbbbbb-bbbb-cccc-dddd-eeeeeeeeeeee'}, {'boot_id':str(uuid4())}, {'session_id':str(uuid4())}])
def test_scope_mismatch_never_writes(event_env, change):
    assert post(event_env, payload(**change)).status_code == 403
    assert not rows(event_env[1])


def test_unauthenticated_malformed_and_oversized_requests_do_not_write(event_env):
    client, db, key, *_ = event_env
    body = payload()
    for headers in ({}, {'Authorization':'Bearer consumer-inference-key'}):
        assert client.post(module.PATH, json=body, headers=headers).status_code == 401
    headers = {'Authorization':'Bearer ' + issue(key, SCOPE)}
    assert client.post(module.PATH, content=b'{bad', headers=headers).status_code == 422
    assert post(event_env, body | {'worker_id':True}).status_code == 422
    assert post(event_env, body | {'expires_at':'2099-01-01T00:00:00Z'}).status_code == 422
    assert client.post(module.PATH, content=(chunk for chunk in (b'a'*2048, b'b'*2049)), headers=headers).status_code == 413
    assert not rows(db)


def test_database_failure_has_sanitized_response(event_env, monkeypatch):
    client, db, *_ = event_env
    def fail():
        raise psycopg.OperationalError('private-db-credential-must-not-appear')
    monkeypatch.setattr(db, 'transaction', fail)
    # The endpoint owns a separate Database instance with the same URL.
    original = module.record
    monkeypatch.setattr(module, 'record', lambda ignored, event, scope: original(db,event,scope))
    response = post(event_env)
    assert response.status_code == 503 and 'private-db' not in response.text


def test_expiry_exact_boundary_uses_server_cutoff(event_env):
    _, db, *_ = event_env
    ids = [payload() for _ in range(3)]
    for body in ids:
        assert post(event_env, body).status_code == 201
    with db.transaction() as conn:
        for body, offset in zip(ids, (-1,0,1)):
            end = CUTOFF + timedelta(milliseconds=offset)
            conn.execute('UPDATE worker_shutdown_events SET received_at=%s,expires_at=%s WHERE event_id=%s',
                         (end-timedelta(hours=48),end,body['event_id']))
    assert module.cleanup(db, now=CUTOFF) == 2
    assert len(rows(db)) == 1 and str(rows(db)[0]['event_id']) == ids[2]['event_id']
    assert module.cleanup(db, now=CUTOFF+timedelta(milliseconds=1)) == 1
    assert not rows(db)


def insert_expired(db, count):
    with db.transaction() as conn:
        conn.execute('''INSERT INTO worker_shutdown_events
            SELECT gen_random_uuid(),'worker.shutdown.requested',1,42,%s,%s,%s,%s,%s,%s
            FROM generate_series(1,%s)''',
            (SCOPE['gpu_uuid'], SCOPE['boot_id'], SCOPE['session_id'], CUTOFF,
             CUTOFF-timedelta(hours=49), CUTOFF-timedelta(hours=1),count))


def test_parallel_cleanup_is_bounded_and_only_changes_event_rows(event_env):
    _, db, *_ = event_env
    assert post(event_env).status_code == 201
    insert_expired(db, 1002)
    with db.transaction() as conn:
        baseline = {table:conn.execute('SELECT count(*) n FROM '+table).fetchone()['n']
                    for table in ('tasks','task_attempts','maintenance')}
    with ThreadPoolExecutor(max_workers=2) as pool:
        deleted = list(pool.map(lambda _:module.cleanup(db,now=CUTOFF), range(2)))
    assert deleted == [500,500]
    assert module.cleanup(db, now=CUTOFF) == 2
    assert module.cleanup(db, now=CUTOFF) == 0
    assert len(rows(db)) == 1
    with db.transaction() as conn:
        assert baseline == {table:conn.execute('SELECT count(*) n FROM '+table).fetchone()['n'] for table in baseline}


def test_cleanup_skips_rows_locked_by_another_process(event_env):
    _, db, *_ = event_env
    insert_expired(db, 2)
    with db.transaction() as conn:
        conn.execute('SELECT event_id FROM worker_shutdown_events LIMIT 1 FOR UPDATE')
        with ThreadPoolExecutor(max_workers=1) as pool:
            assert pool.submit(module.cleanup,db,now=CUTOFF).result(timeout=2) == 1
    assert module.cleanup(db,now=CUTOFF) == 1


def test_api_restart_keeps_events_and_cleans_even_when_receiver_disabled(event_env, monkeypatch):
    _, db, _, settings, auth = event_env
    future = payload()
    assert post(event_env, future).status_code == 201
    insert_expired(db, 1)
    with db.transaction() as conn:
        conn.execute("UPDATE worker_shutdown_events SET received_at=statement_timestamp()-interval '49 hours', expires_at=statement_timestamp()-interval '1 hour' WHERE event_id<>%s", (future['event_id'],))
    complete = Event()
    cleanup = module.cleanup
    def tracking(database):
        result = cleanup(database)
        complete.set()
        return result
    monkeypatch.setattr(module, 'cleanup', tracking)
    monkeypatch.setattr(api_module, 'shutdown_cleanup', module.periodic_cleanup)
    settings.shutdown_event_key_file = None
    with TestClient(api_module.create_app(settings,auth)) as restarted:
        assert complete.wait(2)
        assert restarted.post(module.PATH,json=payload()).status_code == 404
        assert [str(row['event_id']) for row in rows(db)] == [future['event_id']]


def test_cleanup_loop_logs_no_private_exception_and_stops(monkeypatch, caplog):
    async def exercise():
        stop = asyncio.Event()
        def fail(db):
            stop.set()
            raise RuntimeError('private-cleanup-secret')
        monkeypatch.setattr(module,'cleanup',fail)
        await module.periodic_cleanup(None,stop)
    asyncio.run(exercise())
    assert 'shutdown_event_cleanup_unavailable' in caplog.text and 'private-cleanup-secret' not in caplog.text


def test_private_cleanup_command_needs_no_inference_credentials(event_env, monkeypatch, capsys):
    import sys
    from maas_platform import cli
    _, db, *_ = event_env
    insert_expired(db, 1)
    with db.transaction() as conn:
        conn.execute("UPDATE worker_shutdown_events SET received_at=statement_timestamp()-interval '49 hours',expires_at=statement_timestamp()-interval '1 hour'")
    monkeypatch.setattr(cli.Settings, 'from_env', lambda:SimpleNamespace(database_url=db.url))
    monkeypatch.setattr(sys, 'argv', ['cli','shutdown-events-cleanup'])
    cli.main()
    assert json.loads(capsys.readouterr().out) == {'deleted_events':1}
    assert not rows(db)
