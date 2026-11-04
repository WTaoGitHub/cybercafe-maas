import copy
from urllib.parse import urlsplit

import pytest

from maas_platform.replica_controller import Controller
from maas_platform.replica_policy import Service, plan


GIB = 1024**3
SERVICES = tuple(Service(int(gib * GIB), priority, weight, weight)
                 for gib, priority, weight in [(2, 1, 2), (3, 1, 3), (3.5, 2, 5)])


@pytest.mark.parametrize('budgets, current, target', [
    ([], (0, 0, 0), (0, 0, 0)),
    ([3.5], (0, 0, 0), (1, 0, 0)),
    ([5], (0, 0, 0), (1, 1, 0)),
    ([3.5], (1, 1, 0), (1, 1, 1)),
    ([8.5], (0, 0, 0), (1, 1, 1)),
    ([6, 2.5], (0, 0, 0), (1, 1, 0)),
    ([15], (0, 0, 0), (1, 2, 2)),
    ([7, 15], (1, 1, 1), (2, 3, 5)),
    ([15.5, 15], (0, 0, 0), (2, 3, 5)),
    ([15.5, 15], (2, 3, 5), (2, 3, 5)),
])
def test_per_gpu_capacity_and_additive_goals(budgets, current, target):
    assert plan(SERVICES, current, tuple(int(g * GIB) for g in budgets)) == target


def worker(ident, free, state='ready', gpu=None):
    return {'id': ident, 'state': state, 'unreachable': False, 'system_reserved': {'vram': 0},
            'status': {'gpu_devices': [{'uuid': gpu or str(ident),
                                       'memory': {'total': int(free * GIB), 'allocated': 0}}]}}


@pytest.fixture
def trial():
    backend = 'test-custom'
    specs = [{'id': i, 'name': f'maas-replica-pilot-{i}', 'claim_bytes': s.claim,
              'priority': s.priority, 'weight': s.weight, 'limit': s.limit}
             for i, s in enumerate(SERVICES, 1)]
    models = {
        s['id']: {'id': s['id'], 'name': s['name'], 'replicas': 0, 'cluster_id': 4,
                  'backend': backend, 'cpu_offloading': False,
                  'distributed_inference_across_workers': False,
                  'env': {'GPUSTACK_MODEL_VRAM_CLAIM': str(s['claim_bytes'])}}
        for s in specs
    }
    workers = [worker(7, 5), worker(8, 15, state='not_ready')]
    instances = {i: [] for i in models}
    events, writes = [], []

    def api(url, payload=None):
        path = urlsplit(url).path
        if path == '/v2/workers':
            return {'items': copy.deepcopy(workers)}
        if path == '/v2/models':
            return {'items': list(models.values())}
        i = int(path.split('/')[3])
        if path.endswith('/instances'):
            return {'items': instances[i]}
        if payload:
            assert set(k for k in payload if payload[k] != models[i][k]) == {'replicas'}
            models[i] = copy.deepcopy(payload)
            writes.append(i)
        return copy.deepcopy(models[i])

    controller = Controller(api, {'cluster_id': 4, 'backend': backend, 'services': specs},
                            events.append)
    return controller, workers, models, instances, events, writes


def test_reconcile_only_adds_after_native_convergence(trial):
    controller, workers, models, instances, events, writes = trial
    controller.reconcile()
    assert events[-1]['target'] == (1, 1, 0) and writes == []
    controller.reconcile(apply=True)
    controller.reconcile(apply=True)
    assert writes == [1]
    assert events[-1]['reason'] == 'waiting_for_native_convergence'
    instances[1] = [{'state': 'running', 'worker_id': 7}]
    workers[0]['status']['gpu_devices'][0]['memory']['allocated'] = 2 * GIB
    controller.reconcile(apply=True)
    assert writes == [1, 2]
    instances[2] = [{'state': 'running', 'worker_id': 7}]
    workers[0]['status']['gpu_devices'][0]['memory']['allocated'] = 5 * GIB
    workers[1]['state'] = 'ready'
    controller.reconcile(apply=True)
    assert writes == [1, 2, 3]


def test_duplicate_gpu_and_maintenance_do_not_supply_capacity(trial):
    controller, workers, _, _, events, writes = trial
    workers[1] = worker(8, 15, gpu='7')
    controller.reconcile(apply=True)
    assert not writes and events[-1]['reason'] == 'duplicate_gpu'
    workers[1]['maintenance'] = {'enabled': True}
    controller.reconcile()
    assert events[-1]['free_bytes_by_worker'] == {7: 5 * GIB}


def test_unmanaged_active_model_blocks_trial(trial):
    controller, _, models, _, _, writes = trial
    models[99] = {'id': 99, 'cluster_id': 4, 'replicas': 1}
    with pytest.raises(ValueError, match='without other active deployments'):
        controller.reconcile(apply=True)
    assert not writes


def test_watch_triggers_reconciliation_and_bounds_reconnect(monkeypatch):
    from contextlib import nullcontext
    from threading import Event

    from maas_platform.replica_controller import NativeAPI

    api = NativeAPI('test')
    changed, stop = Event(), Event()
    events, calls = [], []

    def open_stream(path, timeout):
        calls.append(path)
        return nullcontext(iter([
            b'{"data":{"id":8,"state":"ready","unreachable":false}}\n',
            b'\n',
            b'[]\n',
        ]))

    monkeypatch.setattr(api, 'open', open_stream)
    monkeypatch.setattr(stop, 'wait', lambda _: False)
    api.watch(4, changed, stop, events.append)
    assert len(calls) == 2 and all(path == '/v2/workers?cluster_id=4&watch=true' for path in calls)
    assert changed.is_set()
    assert [entry['phase'] for entry in events] == [
        'worker_event', 'watch_interrupted', 'watch_interrupted', 'watch_stopped',
    ]


def test_manual_floor_recovery_does_not_mutate_deployments(trial):
    controller, workers, models, instances, events, writes = trial
    workers[0] = worker(7, 1)
    models[1]['replicas'] = 1
    models[2]['replicas'] = 1
    models[3]['replicas'] = 3
    instances[1] = [{'state': 'running', 'worker_id': 7}]
    instances[2] = [{'state': 'pending', 'worker_id': None}]
    instances[3] = [{'state': 'running', 'worker_id': 7}] * 3
    controller.reconcile(apply=True)
    assert not writes
    assert events[-1]['manual_floor_recovery'] == {
        'model_id': 2, 'release_on_worker_id': 7, 'remove_running': {3: 1},
    }


def test_floor_recovery_keeps_last_instance_and_checks_same_card():
    from maas_platform.replica_policy import floor_recovery_advice

    assert floor_recovery_advice(SERVICES, {7: (1, 0, 1)}, {7: GIB}) is None
    assert floor_recovery_advice(SERVICES, {7: (2, 0, 0), 8: (0, 0, 2)},
                                 {7: GIB // 2, 8: 0}) == (1, 8, (0, 0, 1))
    assert floor_recovery_advice(SERVICES, {7: (1, 0, 3)}, {7: 3 * GIB}) is None
