"""Fault and recovery checks using the shared authority and a disposable PostgreSQL."""
import asyncio
import base64
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import json
import time
from threading import Barrier

from fastapi import HTTPException
import httpx
import psycopg
import pytest
from psycopg.types.json import Jsonb

from maas_platform.capacity import Capacity, catalog_routes, ready_candidates
from maas_platform.capacity_admission import Admission, configuration, isolate_budget
from maas_platform.capacity_observe import status
from maas_platform.errors import ServiceError
from maas_platform.guard_topology import verify_processes
from maas_platform.pp_ocr_execution import TABLE
from maas_platform.quarantine import isolate_unknown
from test_capacity import topology
from test_consumer_ocr import png
from test_integration import headers
from test_pp_ocr_execution_integration import env, guarded


@pytest.fixture
def protected(guarded, tmp_path, monkeypatch, request):
    client, service, replicas = guarded
    settings = service.settings
    alias = 'ocr.default'
    raw, _, native = topology(2)
    workers, models, instances, routes, _ = native
    contract = 'test-agent-mandatory-guard'
    raw.update(global_limit=None, sync_percent=25, replica_source='verified_topology')
    pool = raw['pools'].pop('arbitrary-model-1')
    raw['pools'] = {'test_agent_pool': pool}
    pool.update(routes={alias:1}, guard_backend_version=contract, hard_limit=None,
                slots_per_replica=getattr(request, 'param', 1))
    catalog = settings.catalog()
    routes[1]['name'] = 'real-ocr'
    models[1]['backend_version'] = contract
    for i, replica in enumerate(replicas, 1):
        host = f'192.0.2.{i}'
        workers[i]['ip'] = host
        workers[i]['status']['gpu_devices'][0]['uuid'] = replica.identity['gpu_uuid']
        instances[i].update(worker_ip=host, port=8080, model_id=1, backend_version=contract)
    settings.capacity_policy_file = tmp_path/'test_agent_capacity.json'
    settings.capacity_policy_file.write_text(json.dumps(raw))
    settings.capacity_policy_file.chmod(0o600)
    policy, _, rev = configuration(settings)
    names = catalog_routes(policy, catalog)
    capacity = Capacity(policy, 1, 30)
    http = httpx.AsyncClient
    def proof(req):
        assert 'authorization' not in req.headers
        guard = replicas[int(req.url.host.split('.')[-1])-1]
        return httpx.Response(200, json=guard.proof(req.url.params['nonce']))
    def observe(at):
        candidates, reasons = ready_candidates(policy, names, *native)
        gateway_file = tmp_path/'test_agent_gateway.json'
        gateway_file.write_text(json.dumps({'observed':time.time(),'routes':{'1':{
            str(i):[1,r['worker_ip'],r['port']] for i,r in instances.items() if r['state']=='running'
            and not r.get('test_gateway_excluded')}}}))
        gateway_file.chmod(0o600)
        with monkeypatch.context() as patch:
            patch.setattr('maas_platform.guard_topology.httpx.AsyncClient',
                          lambda **kw: http(transport=httpx.MockTransport(proof), **kw))
            proofs = asyncio.run(verify_processes(policy, names, native, candidates, reasons, settings.pp_ocr_worker_keys_file, gateway_file))
        budget = capacity.update(at, candidates, reasons, proofs)
        with service.db.transaction() as c:
            c.execute("""INSERT INTO capacity_snapshot(id,policy_hash,observed_at,expires_at,budget)
                VALUES(1,%s,now(),now()+interval '30 seconds',%s)
                ON CONFLICT (id) DO UPDATE SET budget=excluded.budget,expires_at=excluded.expires_at""",
                      (rev, Jsonb(budget)))
        return budget
    observe(1)
    assert observe(2)['global']['total'] == 2 * pool['slots_per_replica']
    service.pause(True, 'test_agent_activate')
    service.set_capacity_mode(True)
    service.pause(False)
    who = service.auth.authenticate('gpustack_registered_a')
    return client, service, replicas, who, observe, instances, rev


def create_attempt(f, kind, idem='test_agent_scope', **task_options):
    client, service, _, who, *_ = f
    if kind == 'sync':
        ingress = client.app.state.sync_ingress
        token = ingress.begin(who, 'ocr.default', 30)
        return token, lambda: ingress.finish(token, False)
    image = base64.b64encode(png()).decode()
    r = client.post('/maas/v1/tasks', headers=headers(idem=idem), json={**task_options,
        'model':'ocr.default','input':{'image':{'image_base64':image}}})
    assert r.status_code == 202
    task = service.claim()
    assert service.begin_backend(task) == 'real-ocr'
    return task['token'], lambda: service.finish(task)


@pytest.mark.parametrize('kind', ['sync', 'task'])
def test_gpustack_routed_unknown_reconciles_without_blocking_the_same_gpu(guarded, tmp_path, kind):
    from maas_platform.capacity import ready_replica_budget
    from maas_platform.pp_ocr_execution import GPUEvent
    from maas_platform.receipt_reconcile import reconcile

    client, service, replicas = guarded
    settings = service.settings
    raw, _, _ = topology(1)
    pool = raw['pools']['arbitrary-model-1']
    pool.update(routes={'ocr.default': 1}, gateway_verified=False)
    settings.capacity_policy_file = tmp_path / 'test_agent_ready.json'
    settings.capacity_policy_file.write_text(json.dumps(dict(sync_percent=0, pools={'pp': pool})))
    settings.capacity_policy_file.chmod(0o600)
    policy, _, revision = configuration(settings)
    budget = ready_replica_budget(policy, {1: dict(cluster_id=1, owner_principal_id=4, ready_replicas=1)})
    with service.db.transaction() as c:
        c.execute("""INSERT INTO capacity_snapshot(id,policy_hash,observed_at,expires_at,budget)
                     VALUES(1,%s,now(),now()+interval '30 seconds',%s)""",(revision,Jsonb(budget)))
    service.pause(True)
    service.set_capacity_mode(True)
    service.pause(False)
    who = service.auth.authenticate('gpustack_registered_a')
    f = (client, service, replicas, who)
    token, disconnect = create_attempt(f, kind)
    attempt = kind+'.'+token
    replica = replicas[0]
    replica.report(attempt, finished=False)
    disconnect()
    assert isolate_unknown(service.db, kind, token, replica.identity['gpu_uuid'], 'test_agent_original_landing')
    fresh, _ = create_attempt(f, kind, idem='test_agent_next')
    replica.report(kind+'.'+fresh, finished=False)
    with service.db.transaction() as c:
        row = c.execute(f'SELECT state,gpustack_routed,guard_scope FROM {TABLE[kind]} WHERE token=%s',(token,)).fetchone()
        assert row == {'state':'UNKNOWN', 'gpustack_routed': True, 'guard_scope':None}
        assert Admission(c,settings,revision,include_preparing=True).total == 1
    event = GPUEvent(attempt=attempt, finished=True, **replica.identity)
    assert reconcile(service.db, event, apply=True) == 'recovered'
    assert reconcile(service.db, event, apply=True) == 'settled'


@pytest.mark.parametrize('kind', ['sync', 'task'])
def test_committed_gpu_isolation_wakes_observer_and_finish_wakes_it_again(protected, kind):
    from maas_platform.capacity_observe import watch_quarantine

    _, service, (worker, _), *_ = protected
    token, disconnect = create_attempt(protected, kind)
    worker.report(kind + '.' + token, finished=False)

    async def check():
        wake = asyncio.Event()
        listener = asyncio.create_task(watch_quarantine(service.settings.database_url, wake))
        try:
            await asyncio.wait_for(wake.wait(), 3)
            wake.clear()
            with psycopg.connect(service.settings.database_url) as writer:
                writer.execute(f"UPDATE {TABLE[kind]} SET state='UNKNOWN' WHERE token=%s", (token,))
                writer.rollback()
            with pytest.raises(TimeoutError):
                await asyncio.wait_for(wake.wait(), 0.1)
            await asyncio.to_thread(disconnect)
            await asyncio.wait_for(wake.wait(), 3)
            wake.clear()
            await asyncio.to_thread(worker.report, kind + '.' + token, finished=True)
            await asyncio.wait_for(wake.wait(), 3)
        finally:
            listener.cancel()
            await asyncio.gather(listener, return_exceptions=True)
    asyncio.run(check())


@pytest.mark.parametrize('kind', ['task','sync'])
def test_fence_closes_only_unstarted_original_attempt(protected, kind):
    client, service, (first, _), *_ = protected
    token, lose = create_attempt(protected, kind)
    lose()
    with service.db.transaction() as c:
        row = c.execute(f'SELECT state,guard_scope,resolution_reason,gpu_uuid FROM {TABLE[kind]} WHERE token=%s',
                        (token,)).fetchone()
        assert row['state'] == 'RESOLVED' and row['guard_scope'] and not row['gpu_uuid']
        assert 'before any GPU grant' in row['resolution_reason']
        assert c.execute('SELECT count(*) n FROM inference_slots').fetchone()['n'] == 0
        assert c.execute('SELECT count(*) n FROM ocr_usage_records').fetchone()['n'] == 0
        if kind == 'task':
            assert c.execute('SELECT state,error_code FROM tasks').fetchone() == {
                'state':'FAILED','error_code':'EXECUTION_NOT_STARTED'}
    with pytest.raises(HTTPException, match='409'):
        first.run(kind+'.'+token, lambda _:pytest.fail('late engine entry'),None)


@pytest.mark.parametrize('kind',['sync','task'])
def test_racing_close_and_gpu_start_are_mutually_exclusive(protected, kind):
    _, service, (first, _), *_ = protected
    token, lose = create_attempt(protected, kind)
    barrier = Barrier(2)
    def grant():
        barrier.wait()
        try:
            first.report(kind+'.'+token, finished=False)
            return True
        except HTTPException as exc:
            assert exc.status_code == 409
            return False
    with ThreadPoolExecutor(max_workers=1) as workers:
        future = workers.submit(grant)
        barrier.wait()
        lose()
        started = future.result(5)
    with service.db.transaction() as c:
        row = c.execute(f'SELECT state,gpu_uuid FROM {TABLE[kind]} WHERE token=%s',(token,)).fetchone()
    assert (row['state'],row['gpu_uuid']) == (
        ('UNKNOWN',first.identity['gpu_uuid']) if started else ('RESOLVED',None))


def test_isolated_gpu_leave_return_and_late_receipt(protected):
    client, service, (lost, healthy), who, observe, instances, rev = protected
    ingress = client.app.state.sync_ingress
    token = 'sync.'+ingress.begin(who,'ocr.default',30)
    lost.report(token,finished=False)
    ingress.finish(token.split('.')[1],False)
    def check():
        budget = status(service.db)
        assert budget['global'] == {'total':1,'sync_reserved':1,'async_limit':0}
        assert budget['unknown_capacity'] == {'unresolved':1,'covered_by_gpu_exclusion':1,'charged_as_occupancy':0}
        with service.db.transaction() as c:
            a=Admission(c,service.settings,rev,include_preparing=True)
            assert a.total == 0 and a.project_limit == 1
            assert a.allows('ocr.default',who.project,asynchronous=False)
            assert not a.allows('ocr.default',who.project,asynchronous=True)
    assert status(service.db)['pools']['test_agent_pool']['reason']=='gateway_reaches_isolated_gpu'
    assert status(service.db)['global']['total'] == 0
    from maas_platform.maintenance import Maintenance
    from maas_platform.observability import prometheus, snapshot
    data = snapshot(service)
    assert data['unknown_capacity']['excluded_slots'] == 2
    assert 'maas_unknown_capacity_charged_as_occupancy 0' in prometheus(data)
    activity = Maintenance(service).status()['sync_activity'][0]
    assert activity['pending_reason'] == 'awaiting_gpu_finish' and activity['dispatch_age_seconds'] >= 0
    old = instances.pop(1)
    observe(3)
    check()
    other = 'sync.'+ingress.begin(who,'ocr.default',30)
    with pytest.raises(HTTPException, match='409'):
        lost.report(other,finished=False)
    healthy.report(other,finished=False)
    healthy.report_finished(other)
    ingress.finish(other.split('.')[1],True)
    instances[1] = old
    # Even a not-yet-budgeted returning process can receive a gateway retry.
    observe(4)
    assert status(service.db)['global']['total'] == 0
    observe(5)
    assert status(service.db)['global']['total'] == 0
    lost.report_finished(token)
    lost.report_finished(token)
    assert status(service.db)['global']['total'] == 2
    assert service.metrics()['unknown_sync_activity'] == 0


def test_historical_scope_requires_evidence_and_does_not_backfill_grant(protected):
    _, service, (lost, healthy), _, *_ = protected
    token, lose=create_attempt(protected,'sync')
    with service.db.transaction() as c:
        c.execute('UPDATE sync_activity SET guard_scope=NULL WHERE token=%s',(token,))
    lose()
    evidence='test_agent_gateway_retry_and_landing_evidence'
    for _ in range(2):
        assert isolate_unknown(service.db,'sync',token,lost.identity['gpu_uuid'],evidence)
    assert not isolate_unknown(service.db,'sync',token,healthy.identity['gpu_uuid'],evidence)
    assert status(service.db)['global']['total']==0
    protected[5].pop(1)
    protected[4](3)
    assert service.activate_isolated_capacity()['total']==1
    with service.db.transaction() as c:
        r=c.execute('SELECT state,gpu_uuid,gpu_finished_at,guard_scope FROM sync_activity WHERE token=%s',(token,)).fetchone()
        assert r==dict(state='UNKNOWN',gpu_uuid=None,gpu_finished_at=None,guard_scope=None)


def test_cold_cutover_requires_fresh_guarded_zero_and_recovers(protected):
    _, service, (lost, _), who, observe, instances, rev = protected
    token, lose = create_attempt(protected, 'sync')
    with service.db.transaction() as c:
        c.execute('UPDATE sync_activity SET guard_scope=NULL WHERE token=%s', (token,))
    lose()
    with pytest.raises(ServiceError) as denied:
        service.activate_isolated_capacity(at_zero=True)
    assert denied.value.code == 'ISOLATION_REQUIRED'
    assert isolate_unknown(service.db, 'sync', token, lost.identity['gpu_uuid'], 'test_agent_original_gpu')
    instances.pop(1)
    observe(3)
    with pytest.raises(ServiceError, match='unavailable'):
        service.activate_isolated_capacity(at_zero=True)
    healthy = instances.pop(2)
    observe(4)
    with service.db.transaction() as c:
        c.execute("UPDATE maintenance SET capacity_policy_hash='previous-revision'")
        c.execute("""UPDATE capacity_snapshot SET observed_at=now()-interval '31 seconds',
                     expires_at=now()-interval '1 second'""")
    with pytest.raises(ServiceError, match='unavailable'):
        service.activate_isolated_capacity(at_zero=True)
    observe(5)
    with service.db.transaction() as c:
        c.execute("UPDATE capacity_snapshot SET budget=budget #- '{pools,test_agent_pool,processes}'")
    with pytest.raises(ServiceError, match='unavailable'):
        service.activate_isolated_capacity(at_zero=True)
    observe(6)
    with pytest.raises(ServiceError, match='unavailable'):
        service.activate_isolated_capacity()
    assert service.activate_isolated_capacity(at_zero=True) == {'total':0, 'sync_reserved':0, 'async_limit':0}
    with service.db.transaction() as c:
        assert c.execute('SELECT capacity_policy_hash FROM maintenance').fetchone()['capacity_policy_hash'] == rev
        a = Admission(c, service.settings, rev, include_preparing=True)
        assert not any(a.allows('ocr.default', who.project, asynchronous=kind) for kind in (True, False))
    instances[2] = healthy
    observe(7)
    observe(8)
    with service.db.transaction() as c:
        a = Admission(c, service.settings, rev, include_preparing=True)
        assert a.allows('ocr.default', who.project, asynchronous=False)
        assert c.execute('SELECT state FROM sync_activity WHERE token=%s', (token,)).fetchone()['state'] == 'UNKNOWN'


def test_restart_and_missing_proof_reduce_capacity(protected, monkeypatch):
    _, _, (first, _), _, observe, _, _ = protected
    first.identity['instance']='a'*32
    assert observe(3)['global']['total']==1
    assert observe(4)['global']['total']==2
    with monkeypatch.context() as patch:
        patch.setattr(first,'proof',lambda _: {'instance':'a'*32,'signature':'0'*64})
        budget=observe(5)
        assert budget['pools']['test_agent_pool']['reason']=='guard_unverified'
        assert budget['global']['total']==0
    assert observe(6)['global']['total']==0
    assert observe(7)['global']['total']==2


def test_shared_card_removed_from_each_pool_before_caps(protected):
    _, _, (lost, _), _, observe, _, _ = protected
    budget=observe(3)
    pools=budget['pools']
    pools['other']=deepcopy(pools['test_agent_pool'])
    budget['isolation_policy']['pools']['other']=dict(budget['isolation_policy']['pools']['test_agent_pool'])
    budget['global']['total']=4
    for p in pools.values():
        p.pop('processes')
        p['replica_routes'] = dict(p['ready_gpus'])
    two_unknown=[{'isolated_gpu_uuid':lost.identity['gpu_uuid']}] * 2
    isolate_budget(budget,two_unknown)
    assert [p['total'] for p in pools.values()]==[1,1]
    assert budget['global']=={'total':2,'sync_reserved':1,'async_limit':1}


def test_actual_gateway_exclusion_restores_peer_with_worker_still_online(protected):
    client, service, (lost, healthy), who, observe, instances, _ = protected
    ingress = client.app.state.sync_ingress
    token = ingress.begin(who, 'ocr.default', 30)
    lost.report('sync.' + token, finished=False)
    ingress.finish(token, False)
    instances[1]['test_gateway_excluded'] = True
    observe(3)
    assert status(service.db)['global']['total'] == 1
    other = ingress.begin(who, 'ocr.default', 30)
    with pytest.raises(HTTPException, match='409'):
        lost.report('sync.'+other, finished=False)
    healthy.report('sync.'+other, finished=False)
    healthy.report_finished('sync.'+other)
    ingress.finish(other, True)
    assert service.metrics()['unknown_sync_activity'] == 1


@pytest.mark.parametrize('kind', ['sync','task'])
def test_recovery_after_control_process_crash(protected, kind):
    _, service, (first, _), *_ = protected
    token, _ = create_attempt(protected, kind)
    with service.db.transaction() as c:
        if kind == 'sync':
            c.execute("UPDATE sync_activity SET deadline=now()-interval '1 second' WHERE token=%s",(token,))
        else:
            c.execute("UPDATE tasks SET lease_until=now()-interval '1 second' WHERE token=%s",(token,))
    service.recover()
    service.recover()
    with service.db.transaction() as c:
        assert c.execute(f'SELECT state FROM {TABLE[kind]} WHERE token=%s',(token,)).fetchone()['state']=='RESOLVED'
    with pytest.raises(HTTPException, match='409'):
        first.report(kind+'.'+token, finished=False)


@pytest.mark.parametrize('kind', ['task','sync'])
def test_success_without_grant_overrides_negative_evidence(protected, kind):
    from maas_platform.pp_ocr_execution import check_success_headers
    _, service, (first, _), *_ = protected
    token, lose = create_attempt(protected, kind)
    lose()
    check_success_headers(service.db, kind, token)
    with service.db.transaction() as c:
        row = c.execute(f'SELECT state, guard_conflict FROM {TABLE[kind]} WHERE token=%s',(token,)).fetchone()
    assert row == {'state':'UNKNOWN', 'guard_conflict':True}
    with pytest.raises(HTTPException, match='409'):
        first.report(kind+'.'+token, finished=False)


def test_replaced_process_cannot_claim_an_earlier_dispatch(protected):
    _, _, (first, _), _, observe, _, _ = protected
    token, lose = create_attempt(protected, 'sync')
    first.identity['instance'] = 'b' * 32
    observe(3)
    observe(4)
    with pytest.raises(HTTPException, match='409'):
        first.report('sync.' + token, finished=False)
    lose()


def test_missing_gateway_attestation_fails_only_opted_pool(protected):
    _, service, _, _, _, instances, _ = protected
    _, _, native = topology(2)
    workers, models, _, routes, _ = native
    policy, _, _ = configuration(service.settings)
    names = catalog_routes(policy, service.settings.catalog())
    models[1]['backend_version'] = policy.pools['test_agent_pool'].guard_backend_version
    routes[1]['name'] = 'real-ocr'
    native = (workers, models, instances, routes, native[4])
    candidates, reasons = ready_candidates(policy, names, *native)
    proofs = asyncio.run(verify_processes(policy, names, native, candidates, reasons,
                                          service.settings.pp_ocr_worker_keys_file))
    assert proofs == {}
    assert candidates['test_agent_pool'] == {}
    assert reasons['test_agent_pool'] == 'guard_unverified'


def test_signed_receipts_require_matching_identity_and_release_once(protected, tmp_path, monkeypatch):
    """Real task DB and guard callbacks, synthetic engine; see INFERENCE-QUARANTINE.md."""
    from collections import Counter
    from urllib.error import URLError
    from maas_platform.receipt_reconcile import scan

    _, service, (owner, foreign), *_ = protected
    root = tmp_path / 'test_agent_receipts'
    for gpu in (owner, foreign):
        gpu.receipt_dir = root / gpu.identity['gpu_uuid']
        gpu.receipt_dir.mkdir(mode=0o700, parents=True)
    keys = json.loads(service.settings.pp_ocr_worker_keys_file.read_text())
    def check(apply):
        return Counter(scan(service.db, root, keys, ['real-ocr'], apply))

    for i in range(2):
        kind = 'task' if i % 2 else 'sync'
        token, lose = create_attempt(protected, kind, idem=f'test_agent_lost_{i}')
        attempt = kind + '.' + token
        sends = []
        send = owner.open
        def unavailable(req, timeout):
            if json.loads(req.data)['finished']:
                sends.append(req)
                raise URLError('test_agent_dropped')
            return send(req, timeout)
        with monkeypatch.context() as patch:
            patch.setattr(owner, 'open', unavailable)
            with pytest.raises(HTTPException, match='503'):
                owner.run(attempt, lambda _: 'test_agent_engine_output', None)
        assert len(sends) == 2
        lose()
        path = owner.receipt_dir / attempt
        saved = path.read_bytes()
        if i == 0:
            path.write_bytes(saved[:-1])
            assert check(True) == {'invalid_or_unreadable': 1}
            path.write_bytes(saved)
            foreign.save_finish(attempt)
            assert check(False) == {'verified': 1, 'mismatch': 1}
            (foreign.receipt_dir / attempt).unlink()
            assert check(False) == {'verified': 1} and path.exists()
        assert check(True) == {'recovered': 1}
        if i == 0:
            path.write_bytes(saved)
            assert check(True) == {'settled': 1}
        assert not path.exists()
    with service.db.transaction() as conn:
        for table in TABLE.values():
            assert conn.execute(f"""SELECT count(*) n FROM {table} WHERE state='COMPLETED'
                AND resolution_reason='Recovered authenticated GPU finish receipt'""").fetchone()['n'] == 1
        assert conn.execute("SELECT count(*) n FROM tasks WHERE state='FAILED' AND attempts=1").fetchone()['n'] == 1
        assert conn.execute('SELECT count(*) n FROM task_usage_events').fetchone()['n'] == 0


def test_transient_failure_before_grant_retries_on_new_token(protected):
    from maas_platform.worker import Worker

    client, service, (owner, _), *_ = protected
    service.settings.retry_unstarted_models = {'ocr.default'}
    auth = headers(idem='test_agent_retry_on_no_grant')
    accepted = client.post('/maas/v1/tasks', headers=auth, json={'model':'ocr.default', 'max_attempts':2,
        'input':{'image':{'image_base64':base64.b64encode(png()).decode()}}})
    assert accepted.status_code == 202
    attempts = []
    def gateway(req):
        token = req.headers['X-MaaS-Execution-Attempt']
        attempts.append(token)
        if len(attempts) == 1:
            return httpx.Response(503)
        output = owner.run(token, lambda _: {'text':'verified', 'lines':[], 'elapsed_ms':1, 'node':'fixture'}, None)
        return httpx.Response(200, json={'choices':[{'message':{'content':json.dumps(output)}}]})
    async def execute(task):
        async with httpx.AsyncClient(transport=httpx.MockTransport(gateway)) as transport:
            await Worker(service)._execute(task, transport)
    asyncio.run(execute(service.claim()))
    detail = client.get(accepted.headers['location'],headers=auth).json()
    assert (detail['status'], detail['execution_state']) == ('RETRY_WAIT','RESOLVED')
    with pytest.raises(HTTPException, match='409'):
        owner.report(attempts[0], finished=False)
    with service.db.transaction() as conn:
        conn.execute('UPDATE tasks SET next_run=now() WHERE id=%s', (detail['id'],))
    with ThreadPoolExecutor(2) as pool:
        winners = [t for t in pool.map(lambda _:service.claim(), range(2)) if t]
    assert len(winners) == 1
    asyncio.run(execute(winners[0]))
    detail = client.get(accepted.headers['location'],headers=auth).json()
    assert (detail['status'], detail['execution_state'], detail['attempts']) == ('SUCCEEDED','COMPLETED',2)
    assert len(set(attempts)) == 2 and service.metrics()['successful_tasks'] == 1


@pytest.mark.parametrize('scenario,expected', [
    ('granted', ('FAILED','UNKNOWN_BACKEND_ACTIVITY')),
    ('cancelled', ('CANCELLED',None)),
    ('expired', ('FAILED','DEADLINE_EXCEEDED')),
    ('exhausted', ('FAILED','EXECUTION_NOT_STARTED')),
    ('permanent', ('FAILED','EXECUTION_NOT_STARTED')),
    ('post_execution_error', ('FAILED','STORAGE_QUOTA')),
    ('lease', ('RETRY_WAIT','EXECUTION_NOT_STARTED')),
    ('revision', ('FAILED','MODEL_REVISION_CHANGED')),
])
def test_unstarted_retry_boundaries(protected, scenario, expected):
    _, service, (owner, _), *_ = protected
    service.settings.retry_unstarted_models = {'ocr.default'}
    token, _ = create_attempt(protected, 'task', max_attempts=1 if scenario == 'exhausted' else 2)
    if scenario in ('granted', 'post_execution_error'):
        owner.report('task.'+token, finished=False)
        if scenario == 'post_execution_error':
            owner.report_finished('task.'+token)
    with service.db.transaction() as conn:
        row = conn.execute('SELECT * FROM tasks WHERE token=%s',(token,)).fetchone()
        if scenario == 'cancelled':
            conn.execute("UPDATE tasks SET state='CANCEL_REQUESTED' WHERE token=%s",(token,))
        if scenario == 'expired':
            conn.execute("UPDATE tasks SET deadline=now() WHERE token=%s",(token,))
        if scenario == 'lease':
            conn.execute("UPDATE tasks SET lease_until=now() WHERE token=%s",(token,))
    if scenario == 'revision':
        catalog = service.settings.catalog()
        catalog['ocr.default']['revision'] = 'changed'
        service.settings.catalog_file.write_text(json.dumps(catalog))
    if scenario == 'lease':
        assert service.recover() == 1
    elif scenario == 'post_execution_error':
        assert service.finish(row, error=ServiceError('STORAGE_QUOTA', 'test_agent_quota', 429, True))
    else:
        assert service.finish(row, error=ServiceError('UNKNOWN_BACKEND_ACTIVITY', 'test_agent_failure',
                                                      transient_before_grant=scenario != 'permanent'))
    with service.db.transaction() as conn:
        result = conn.execute('SELECT state,error_code FROM tasks WHERE id=%s',(row['id'],)).fetchone()
    assert (result['state'],result['error_code']) == expected


@pytest.mark.parametrize('protected', [64], indirect=True)
def test_daemon_recovers_fifty_receipts_after_storage_reconnect(protected, tmp_path, monkeypatch):
    """Start the actual CLI in both modes; measure first-visible committed completion."""
    import os
    import subprocess
    import sys
    from urllib.error import URLError

    _, service, (owner, _), *_ = protected
    root = tmp_path/'test_agent_control_volume'
    root.mkdir()
    owner.receipt_dir = tmp_path/'test_agent_disconnected_volume'
    owner.receipt_dir.mkdir(mode=0o700)
    close_attempts = []
    original = owner.open
    dropped = 0

    def lose_reply(req, timeout):
        nonlocal dropped
        if json.loads(req.data)['finished']:
            dropped += 1
            raise URLError('test_agent_lost_callback')
        return original(req, timeout)
    monkeypatch.setattr(owner, 'open', lose_reply)
    for i in range(50):
        kind = 'sync' if i % 2 else 'task'
        token, close = create_attempt(protected, kind, idem=f'test_agent_daemon_{i}')
        with pytest.raises(HTTPException, match='503'):
            owner.run(kind+'.'+token, lambda _: 'test_agent_output', None)
        close_attempts.append(close)
    for close in close_attempts:
        close()
    assert dropped == 100 and len(list(owner.receipt_dir.iterdir())) == 50
    args = [sys.executable,'-B','-m','maas_platform.receipt_reconcile','--root',str(root),'--route','real-ocr']
    env = {**os.environ, 'MAAS_DATABASE_URL': service.settings.database_url,
           'MAAS_PP_OCR_WORKER_KEYS_FILE': str(service.settings.pp_ocr_worker_keys_file)}
    visible = root/owner.identity['gpu_uuid']
    owner.receipt_dir.rename(visible)
    shadow = subprocess.run([*args,'--once'], env=env, capture_output=True, text=True, timeout=20, check=True)
    assert "'verified': 50" in shadow.stderr and len(list(visible.iterdir())) == 50
    visible.rename(owner.receipt_dir)
    # Let a fresh process enter its empty-volume poll before making the records visible to it.
    command = subprocess.Popen([*args,'--mode','apply'], env=env)
    try:
        time.sleep(1)
        observed = {}
        start = time.monotonic()
        owner.receipt_dir.rename(visible)
        while len(observed) < 50 and time.monotonic() - start < 60:
            assert command.poll() is None
            with service.db.transaction() as conn:
                rows = conn.execute("""SELECT token FROM backend_activity WHERE state='COMPLETED'
                    UNION ALL SELECT token FROM sync_activity WHERE state='COMPLETED'""").fetchall()
            elapsed = time.monotonic() - start
            for row in rows:
                observed.setdefault(row['token'], elapsed)
            if len(observed) < 50:
                time.sleep(.05)
        assert len(observed) == 50
        # The last DB commit may be visible immediately before its acknowledged file is removed.
        until = time.monotonic() + 5
        while any(visible.iterdir()) and time.monotonic() < until:
            time.sleep(.01)
        assert not list(visible.iterdir())
    finally:
        command.terminate()
        try:
            command.wait(timeout=10)
        except subprocess.TimeoutExpired:
            command.kill()
            command.wait()
    with service.db.transaction() as conn:
        assert conn.execute("SELECT count(*) n FROM tasks WHERE state='FAILED' AND attempts=1").fetchone()['n'] == 25
        assert conn.execute('SELECT count(*) n FROM task_usage_events').fetchone()['n'] == 0
        assert conn.execute('SELECT count(*) n FROM sync_usage_events').fetchone()['n'] == 0
        assert conn.execute('SELECT count(*) n FROM inference_slots WHERE NOT backend_finished').fetchone()['n'] == 0
    delays = sorted(observed.values())
    print(f'50 recovered, daemon observation seconds P50={delays[24]:.3f} P95={delays[47]:.3f} max={delays[-1]:.3f}')
    assert delays[47] <= 60
