"""Isolated PostgreSQL and simulated worker runtime evidence for E1."""
import json
from concurrent.futures import ThreadPoolExecutor
import pytest
from fastapi import HTTPException

from maas_bootstrap import runtime_witness
from maas_platform.pp_ocr_execution import PATH, TABLE
from maas_platform.runtime_proof import signature
from test_execution_scope_integration import create_attempt, protected
from test_gpu_isolation_integration import pinned
from test_pp_ocr_execution_integration import env, guarded

HOST_KEY = 'e' * 64
IDENTITY = {'boot': '11111111-1111-4111-8111-111111111111', 'container': 'a' * 64,
            'cgroup': '/docker/' + 'a' * 64, 'inode': 123, 'started': '2026-11-25T00:00:00Z', 'pid': 321}


def enable(protected, path):
    client, service, (owner, other), *_ = protected
    gpu = owner.identity['gpu_uuid']
    keys = path / 'test_agent_host_keys'
    keys.write_text(json.dumps({gpu: HOST_KEY}))
    keys.chmod(0o600)
    service.settings.runtime_proof_keys_file = keys
    value = {**owner.identity, 'identity': IDENTITY}
    owner.runtime_binding = {'identity': IDENTITY, 'signature': signature(HOST_KEY, 'bind', value)}
    return client, service, owner, other


def request(client, gpu, operation='runtime_poll', key=HOST_KEY, **fields):
    return client.post(PATH, headers={'Authorization':'Bearer '+key},
                       json={'operation': operation, 'gpu_uuid': gpu, **fields})


def report(client, gpu, check, outcome='terminated', key=HOST_KEY, payload_overrides=None):
    body = {**check, 'gpu_uuid': gpu, 'outcome': outcome, **(payload_overrides or {})}
    return request(client, gpu, 'runtime_report', key, instance=check['instance'], nonce=check['nonce'],
                   outcome=outcome, signature=signature(HOST_KEY, 'inspect', body))


def age(service):
    with service.db.transaction() as conn:
        for table in TABLE.values():
            conn.execute(f"UPDATE {table} SET unknown_since=now()-interval '31 seconds' WHERE state='UNKNOWN'")
        conn.execute("UPDATE execution_runtime SET next_check_at=now()")


@pytest.mark.parametrize('protected', [32], indirect=True)
def test_observe_and_apply_terminated_runtime_twenty_attempts(protected, tmp_path):
    client, service, owner, _ = enable(protected, tmp_path)
    gpu = owner.identity['gpu_uuid']
    assert request(client, gpu, key='1'*64).status_code == 403
    closers = []
    for i in range(20):
        kind = 'task' if i % 2 == 0 else 'sync'
        token, close = create_attempt(protected, kind, idem=f'test_agent_termination_{i}')
        owner.report(kind+'.'+token, finished=False)
        closers.append(close)
    for close in closers:
        close()
    assert request(client, gpu).json() == {'checks': []}
    age(service)
    with ThreadPoolExecutor(max_workers=3) as pool:
        batches = list(pool.map(lambda _:request(client, gpu).json()['checks'], range(3)))
    assert sorted(map(len,batches)) == [0,0,1]
    check = next(batch[0] for batch in batches if batch)
    assert check['identity'] == IDENTITY
    assert report(client, gpu, check, payload_overrides={'identity':{**IDENTITY, 'pid':777}}).status_code == 403
    assert report(client, gpu, check, outcome='present').status_code == 204
    assert service.metrics()['unknown_backend_activity'] == 10
    age(service)
    check, = request(client, gpu).json()['checks']
    assert report(client, gpu, check).status_code == 204
    assert report(client, gpu, check).status_code == 409
    assert service.metrics()['unknown_sync_activity'] == 10  # Observation records evidence but does not settle.
    service.settings.runtime_proof_apply_routes = {'another-route'}
    assert request(client, gpu).status_code == 200
    assert service.metrics()['unknown_sync_activity'] == 10
    service.settings.runtime_proof_apply_routes = {'real-ocr'}
    assert request(client, gpu).status_code == 200
    with service.db.transaction() as conn:
        for table in TABLE.values():
            row = conn.execute(f"""SELECT count(*) n FROM {table} WHERE state='RESOLVED'
                AND gpu_finished_at IS NULL AND resolution_reason LIKE 'Original guarded runtime terminated%%'""").fetchone()
            assert row['n'] == 10
        assert conn.execute("SELECT count(*) n FROM tasks WHERE state='FAILED' AND attempts=1").fetchone()['n'] == 10
        assert conn.execute("SELECT count(*) n FROM task_usage_events").fetchone()['n'] == 0
        assert conn.execute("SELECT count(*) n FROM sync_usage_events").fetchone()['n'] == 0
        assert conn.execute("SELECT count(*) n FROM inference_slots WHERE NOT backend_finished").fetchone()['n'] == 0


def test_expired_evidence_original_finish_wins_and_legacy_untouched(protected, tmp_path):
    client, service, owner, other = enable(protected, tmp_path)
    service.settings.runtime_proof_apply_routes = {'real-ocr'}
    token, close = create_attempt(protected, 'task')
    with pytest.raises(HTTPException, match='503'):
        owner.runtime_binding['signature'] = 'f'*64
        owner.report('task.'+token, finished=False)
    enable(protected, tmp_path)
    owner.report('task.'+token, finished=False)
    close()
    age(service)
    gpu = owner.identity['gpu_uuid']
    check, = request(client, gpu).json()['checks']
    with service.db.transaction() as conn:
        conn.execute("UPDATE execution_runtime SET challenge_until=now()-interval '1 second'")
    assert report(client, gpu, check).status_code == 409
    assert request(client, gpu).json()['checks'] == []
    check, = request(client, gpu, reconnected=True).json()['checks']
    assert request(client, gpu, reconnected=True).json()['checks'] == []
    owner.report_finished('task.'+token)
    assert report(client, gpu, check).status_code == 204
    with service.db.transaction() as conn:
        assert conn.execute('SELECT state FROM backend_activity').fetchone()['state'] == 'COMPLETED'
    legacy, close = create_attempt(protected, 'task', idem='test_agent_legacy')
    other.report('task.'+legacy, finished=False)
    close()
    age(service)
    assert request(client, gpu).json()['checks'] == []
    assert service.metrics()['unknown_backend_activity'] == 1


@pytest.mark.parametrize('case,result', [
    ('live','present'), ('exited','terminated'), ('missing','terminated'), ('still_on_gpu','unconfirmed'),
    ('new_boot','unconfirmed'), ('same_id_new_run','unconfirmed'), ('new_inode','unconfirmed'),
    ('children_remain','present'),
])
def test_host_requires_original_cgroup_and_device_evidence(tmp_path, monkeypatch, case, result):
    identity = dict(IDENTITY)
    boot = tmp_path/'boot'
    boot.write_text(identity['boot'] if case != 'new_boot' else 'later-boot')
    monkeypatch.setattr(runtime_witness, 'BOOT', boot)
    monkeypatch.setattr(runtime_witness, 'CGROUP', tmp_path)
    monkeypatch.setattr(runtime_witness, 'check_cgroup_mount', lambda: None)
    monkeypatch.setattr(runtime_witness, 'original_gpu_released', lambda gpu,pid: case != 'still_on_gpu')
    path = tmp_path/identity['cgroup'].lstrip('/')
    if case != 'missing':
        path.mkdir(parents=True)
        identity['inode'] = path.stat().st_ino + (case == 'new_inode')
        (path/'cgroup.events').write_text('populated '+str(int(case in ('live','children_remain')))+'\nfrozen 0\n')
    monkeypatch.setattr(runtime_witness, 'inspect', lambda _: None if case == 'missing' else {
        'State': {'StartedAt': 'other' if case == 'same_id_new_run' else identity['started'],
                  'Status': 'running' if case == 'live' else 'exited', 'Pid': 321 if case == 'live' else 0}})
    assert runtime_witness.inspect_original(identity, 'fixture-gpu') == result


def test_pinned_gpu_runtime_can_settle_and_peers_keep_dispatching(pinned, tmp_path, monkeypatch):
    client, service, owner, healthy = enable(pinned, tmp_path)
    route = owner.identity['route']
    service.settings.runtime_proof_apply_routes = {route}
    who = service.auth.authenticate('gpustack_registered_a')
    ingress = client.app.state.sync_ingress
    token, target = ingress.begin_dispatch(who, 'ocr.default', 30)
    assert target == route
    owner.report('sync.'+token, finished=False)
    ingress.finish(token, False)
    age(service)
    next_token, target = ingress.begin_dispatch(who, 'ocr.default', 30)
    assert target == healthy.identity['route']
    healthy.report('sync.'+next_token, finished=False)
    healthy.report_finished('sync.'+next_token)
    ingress.finish(next_token, True)

    def exchange(_opener, _origin, key, value):
        response = client.post(PATH, headers={'Authorization': 'Bearer '+key}, json=value)
        assert response.status_code in (200,204)
        return response.json() if response.status_code == 200 else None

    monkeypatch.setattr(runtime_witness, 'exchange', exchange)
    monkeypatch.setattr(runtime_witness, 'inspect_original', lambda identity,gpu: 'terminated')
    runtime_witness.answer(None, '', HOST_KEY, owner.identity['gpu_uuid'], {route})
    assert service.metrics()['unknown_sync_activity'] == 0
    with service.db.transaction() as conn:
        row = conn.execute('SELECT state, runtime_instance, guard_scope FROM sync_activity WHERE token=%s',
                           (token,)).fetchone()
        assert row == {'state': 'RESOLVED', 'runtime_instance': owner.identity['instance'], 'guard_scope': None}


def test_host_binding_reads_actual_peer_cgroup(tmp_path, monkeypatch):
    proc, group = tmp_path/'proc', tmp_path/'cgroup'
    (proc/str(IDENTITY['pid'])).mkdir(parents=True)
    (proc/str(IDENTITY['pid'])/'cgroup').write_text('0::'+IDENTITY['cgroup']+'\n')
    run_group = group / IDENTITY['cgroup'].lstrip('/')
    run_group.mkdir(parents=True)
    (run_group/'cgroup.events').write_text('populated 1\n')
    boot = tmp_path/'boot'
    boot.write_text(IDENTITY['boot'])
    monkeypatch.setattr(runtime_witness, 'PROC', proc)
    monkeypatch.setattr(runtime_witness, 'CGROUP', group)
    monkeypatch.setattr(runtime_witness, 'BOOT', boot)
    monkeypatch.setattr(runtime_witness, 'check_cgroup_mount', lambda: None)
    image = 'sha256:'+'b'*64
    routes = {'test_agent_a': image, 'test_agent_shared': image}
    row = {'Image': image, 'Path': 'python3', 'Args': ['/app/server.py'], 'Mounts': [], 'Config': {},
           'HostConfig': {'Privileged': False},
           'State': {'Pid': IDENTITY['pid'], 'Running': True, 'StartedAt': IDENTITY['started']}}
    monkeypatch.setattr(runtime_witness, 'inspect', lambda _: row)
    prefix = 'MAAS_PP_OCR_EXECUTION_ROUTE='
    for route in routes:
        row['Config']['Env'] = [prefix + route]
        assert runtime_witness.bind_peer(321, routes) == (route, {**IDENTITY, 'inode':run_group.stat().st_ino})
    for rejected in ([], [prefix + 'unlisted'], [prefix + name for name in routes]):
        row['Config']['Env'] = rejected
        with pytest.raises(ValueError, match='containment'):
            runtime_witness.bind_peer(321, routes)
    row['Config']['Env'] = [prefix + 'test_agent_a']
    with pytest.raises(ValueError, match='containment'):
        runtime_witness.bind_peer(321, {'test_agent_a': 'sha256:' + 'f' * 64})
    row['HostConfig']['Privileged'] = True
    with pytest.raises(ValueError, match='containment'):
        runtime_witness.bind_peer(321, routes)


def test_host_witness_accepts_legacy_or_route_map():
    image = 'sha256:' + 'c' * 64
    legacy = {'route': 'test_agent_a', 'image_id': image}
    routes = {'test_agent_a': image, 'test_agent_shared': image}
    assert runtime_witness.enrolled_routes(legacy) == {'test_agent_a': image}
    assert runtime_witness.enrolled_routes({'routes': routes}) == routes
    for invalid in ({'routes': {}}, {'routes': {'unknown': 'tag'}}, {**legacy, 'routes': routes}):
        with pytest.raises(ValueError):
            runtime_witness.enrolled_routes(invalid)


def test_one_gpu_answers_both_routes_and_ignores_unenrolled_route(monkeypatch):
    gpu = 'GPU-11111111-1111-4111-8111-111111111111'
    enrolled = {'test_agent_a', 'test_agent_shared'}
    checks = [dict(route=name, identity=IDENTITY, instance=str(i)*32, nonce=str(i)*64)
              for i, name in enumerate(['test_agent_a', 'test_agent_shared', 'test_agent_other'], 1)]
    reports = []

    def exchange(opener, origin, key, request):
        if request['operation'] == 'runtime_poll':
            return {'checks': checks}
        reports.append(request)

    monkeypatch.setattr(runtime_witness, 'exchange', exchange)
    monkeypatch.setattr(runtime_witness, 'inspect_original', lambda identity, gpu: 'present')
    runtime_witness.answer(None, '', HOST_KEY, gpu, enrolled)
    assert len(reports) == 2
    for request, check in zip(reports, checks):
        payload = dict(check, gpu_uuid=gpu, outcome='present')
        assert request['instance'] == check['instance']
        assert request['signature'] == signature(HOST_KEY, 'inspect', payload)
