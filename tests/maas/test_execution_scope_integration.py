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
def protected(guarded, tmp_path, monkeypatch):
    client, service, replicas = guarded
    settings = service.settings
    alias = 'ocr.default'
    raw, _, native = topology(2)
    workers, models, instances, routes, _ = native
    contract = 'test-agent-mandatory-guard'
    raw.update(global_limit=None, sync_percent=25)
    pool = raw['pools'].pop('arbitrary-model-1')
    raw['pools'] = {'test_agent_pool': pool}
    pool.update(routes={alias:1}, guard_backend_version=contract, hard_limit=None)
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
    assert observe(2)['global']['total'] == 2
    service.pause(True, 'test_agent_activate')
    service.set_capacity_mode(True)
    service.pause(False)
    who = service.auth.authenticate('gpustack_registered_a')
    return client, service, replicas, who, observe, instances, rev


def create_attempt(f, kind):
    client, service, _, who, *_ = f
    if kind == 'sync':
        ingress = client.app.state.sync_ingress
        token = ingress.begin(who, 'ocr.default', 30)
        return token, lambda: ingress.finish(token, False)
    image = base64.b64encode(png()).decode()
    r = client.post('/maas/v1/tasks', headers=headers(idem='test_agent_scope'), json={
        'model':'ocr.default','input':{'image':{'image_base64':image}}})
    assert r.status_code == 202
    task = service.claim()
    assert service.begin_backend(task) == 'real-ocr'
    return task['token'], lambda: service.finish(task)


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
