from copy import deepcopy
import asyncio
from dataclasses import replace
import json
import os
import subprocess
import sys
from pathlib import Path
from tempfile import TemporaryDirectory
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from maas_platform.replica_observe import native_devices, run
from maas_platform.replica_reconcile import ReconcileObserve
from maas_platform.replica_writer_v2 import ReconcileWriter
from maas_platform.replica_control import control, targets
from maas_replica_execution import PendingWrite
from maas_replica_pressure import PressureGate
from test_replica_reconcile import model, worker, policy
from test_replica_contract import config
from maas_platform.replica_contract import ControlPolicy


class WriterV2Test(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name); self.now = 100
        self.policy = policy('apply', stability=0)
        self.refresh_value = self.policy
        self.rows = {'workers': {4: worker()}, 'models': {1: model()}, 'model-instances': {}}
        self.sample = self.root/'pressure.json'
        self.gate = PressureGate(self.sample, clock=lambda: self.now)
        self.observer = ReconcileObserve(native_devices, self.gate, state_directory=self.root/'state',
                                         clock=lambda: self.now, wall_clock=lambda: self.now)
        self.state = self.observer.state
        self.state.adopt(self.policy, self.rows['models'], expected_targets={1: 0})
        self.journal = PendingWrite(self.root/'write.json')
        self.writes = []; self.outcome = 'success'; self.invalidate_on_get = False
        self.pause_on_refresh = False; self.version = 'v2.2.3'
        self.writer = self.make_writer()

    def make_writer(self):
        return ReconcileWriter(1, {1: 2}, self.journal, self.state, self.request,
                               pressure=PressureGate(self.sample, clock=lambda: self.now),
                               clock=lambda: self.now, wall_clock=lambda: self.now)

    async def listing(self, client, kind):
        return deepcopy(self.rows[kind])

    async def request(self, method, path, body=None):
        if path == '/version':
            return {'version': self.version}
        if path.split('?')[0] in ('/v2/workers', '/v2/models', '/v2/model-instances'):
            key = path.split('?')[0].split('/')[-1]
            values = list(self.rows[key].values())
            return {'items': deepcopy(values), 'pagination': {'page': 1, 'totalPage': 1, 'total': len(values)}}
        self.assertTrue(path.startswith('/v2/models/'))
        model_id = int(path.rsplit('/', 1)[-1])
        row = self.rows['models'][model_id]
        if method == 'GET':
            if self.invalidate_on_get:
                self.refresh_value = policy('apply', stability=0)
            return deepcopy(row)
        self.assertEqual(method, 'PUT'); self.assertIsNotNone(self.journal.read())
        self.writes.append(deepcopy(body))
        if self.outcome == 'before_commit_timeout':
            raise TimeoutError()
        row['replicas'] = body['replicas']
        if self.outcome == 'native_filled_fields':
            row.update(categories=['llm'], distributable=False)
        ident = len(self.rows['model-instances']) + 1
        self.rows['model-instances'][ident] = dict(id=ident, model_id=model_id, worker_id=None,
                                                  gpu_indexes=None, state='pending')
        if self.outcome == 'after_commit_timeout':
            raise TimeoutError()

    async def advance(self):
        await self.observer.tick(None, self.listing, self.policy, subscribed=False,
                                  pending_journal=self.journal)
        def refresh():
            if self.pause_on_refresh:
                self.state.pause()
            return self.refresh_value
        return await self.writer.reconcile(self.policy, self.observer, refresh=refresh)

    def running(self):
        self.rows['models'][1]['ready_replicas'] = 1
        self.rows['model-instances'][1].update(state='running', worker_id=4, gpu_indexes=[0])
        self.rows['workers'][4]['status']['gpu_devices'][0]['memory']['allocated'] = 4

    def parallel(self, *, maximum=4, limit=2, pressure_mode='disabled'):
        raw = config(mode='apply', pressure_mode=pressure_mode)
        raw['models']['1']['max_replicas'] = maximum
        raw['execution'].update(max_inflight_global=limit, max_inflight_per_cluster=limit)
        raw['reconcile']['stability_s'] = 0
        raw['startup'] = {'parallel_startup': True}
        if pressure_mode != 'disabled':
            raw['pressure']['recovery_hold_s'] = 0
        self.policy = ControlPolicy(raw, allow_apply=True); self.refresh_value = self.policy
        self.writer.model_caps[1] = maximum
        self.state.adopt(self.policy, {1: self.rows['models'][1]}, expected_targets={1: 0})

    def assign(self, ident, worker_id):
        self.rows['model-instances'][ident].update(
            state='starting', worker_id=worker_id, gpu_indexes=[0])

    async def test_parallel_starts_second_card_before_first_is_running_despite_allocation_lag(self):
        self.parallel(); self.rows['workers'][5] = worker(5)
        self.assertEqual(await self.advance(), 'confirmed')
        self.assign(1, 4)
        self.assertEqual(await self.advance(), 'confirmed')
        self.assign(2, 5)
        self.assertEqual(await self.advance(), 'idle')
        self.assertEqual([row['replicas'] for row in self.writes], [1, 2])
        self.assertEqual(self.writer.execution_profile, 'parallel_reserved')
        self.assertEqual(self.writer.effective_startup_limit, 2)
        self.assertEqual(self.rows['models'][1]['ready_replicas'], 0)
        # The safety proof withholds whole pending cards even while native
        # allocated telemetry still says zero, rather than fitting four.
        self.assertEqual(self.observer.view['plan']['policy_target'], {1: 2})

    async def test_parallel_caps_one_two_and_four_bound_actual_startup_count(self):
        for limit in (1, 2, 4):
            with self.subTest(limit=limit):
                self.rows = {'workers': {i: worker(i) for i in (4, 5, 6, 7)},
                             'models': {1: model()}, 'model-instances': {}}
                self.writes.clear(); self.parallel(maximum=8, limit=limit)
                for ident in range(1, limit + 1):
                    self.assertEqual(await self.advance(), 'confirmed')
                    self.assign(ident, ident + 3)
                self.assertEqual(await self.advance(), 'idle')
                self.assertEqual(len(self.writes), limit)

    async def test_unassigned_absent_surplus_error_and_distributed_pending_never_release_extra_slot(self):
        self.parallel(); self.rows['workers'][5] = worker(5)
        self.assertEqual(await self.advance(), 'confirmed')
        original = deepcopy(self.rows['model-instances'])
        changes = ({}, None, {'state': 'error'}, {'worker_id': 99, 'gpu_indexes': [0]},
                   {'worker_id': 4, 'gpu_indexes': [0], 'distributed_servers': {'workers': [5]}},
                   {'worker_id': 4, 'gpu_indexes': [0], 'deleted_at': 'now'})
        for change in changes:
            self.rows['model-instances'] = deepcopy(original)
            if change is None:
                self.rows['model-instances'].clear()
            else:
                self.rows['model-instances'][1].update(change)
            self.assertEqual(await self.advance(), 'idle')
            self.assertFalse(self.observer.view['plan']['parallel_safe'])
        self.assign(1, 4)
        self.rows['model-instances'][2] = {**self.rows['model-instances'][1], 'id': 2}
        self.assertEqual(await self.advance(), 'idle')
        self.assertEqual(len(self.writes), 1)

    async def test_unmanaged_pending_in_same_cluster_is_reserved_too(self):
        self.parallel(); self.rows['workers'][5] = worker(5)
        self.rows['models'][2] = {**model(), 'id': 2, 'replicas': 1}
        self.rows['model-instances'][10] = dict(
            id=10, model_id=2, worker_id=None, gpu_indexes=[], state='pending')
        self.assertEqual(await self.advance(), 'idle')
        self.assign(10, 4)
        self.assertEqual(await self.advance(), 'confirmed')
        # One unmanaged and one new managed start exhaust the cluster limit.
        self.assign(2, 5)
        self.assertEqual(await self.advance(), 'idle')
        self.assertEqual(len(self.writes), 1)

    async def test_new_unmanaged_work_after_planning_invalidates_final_full_snapshot(self):
        self.parallel(); self.rows['workers'][5] = worker(5)
        await self.observer.tick(None, self.listing, self.policy, subscribed=False)
        self.rows['models'][2] = {**model(), 'id': 2, 'replicas': 1}
        self.rows['model-instances'][2] = dict(
            id=2, model_id=2, worker_id=5, gpu_indexes=[0], state='starting')
        self.assertEqual(await self.writer.reconcile(self.policy, self.observer,
                                                    refresh=lambda: self.policy), 'native_snapshot_changed')
        self.assertEqual(self.writes, [])

    async def test_blocked_startup_in_one_model_leaves_another_models_reserved_budget_available(self):
        self.rows['workers'][5] = worker(5)
        self.rows['models'][1]['replicas'] = 1
        self.rows['models'][2] = {**model(), 'id': 2}
        self.rows['model-instances'][1] = dict(
            id=1, model_id=1, worker_id=4, gpu_indexes=[0], state='downloading')
        raw = config(mode='apply')
        raw['models'] = {'1': {'vram_claim_bytes': 4, 'max_replicas': 2, 'min_replicas': 1},
                         '2': {'vram_claim_bytes': 4, 'max_replicas': 2, 'min_replicas': 1}}
        raw['startup'] = {'parallel_startup': True}; raw['reconcile']['stability_s'] = 0
        self.policy = ControlPolicy(raw, allow_apply=True); self.refresh_value = self.policy
        self.writer.model_caps = {1: 2, 2: 2}
        self.state.adopt(self.policy, self.rows['models'], expected_targets={1: 1, 2: 0})
        self.assertEqual(await self.advance(), 'confirmed')
        self.assertEqual(self.rows['models'][1]['replicas'], 1)
        self.assertEqual(self.rows['models'][2]['replicas'], 1)
        self.assertEqual(len(self.writes), 1)

    async def test_other_cluster_startup_consumes_global_but_not_local_budget(self):
        self.rows['models'][2] = {**model(), 'id': 2, 'cluster_id': 2, 'replicas': 1}
        self.rows['model-instances'][10] = dict(
            id=10, model_id=2, worker_id=None, gpu_indexes=[], state='pending')
        self.parallel(limit=1)
        self.assertEqual(await self.advance(), 'idle')
        self.assertEqual(self.writes, [])
        self.parallel(limit=2)
        self.assertEqual(await self.advance(), 'confirmed')

    async def test_parallel_advisory_degrades_to_one_slot(self):
        self.parallel(pressure_mode='advisory'); self.rows['workers'][5] = worker(5)
        self.assertEqual(await self.advance(), 'pressure_unknown_grace')
        self.now += 31
        self.assertEqual(await self.advance(), 'confirmed')
        self.assign(1, 4); self.now += 61
        # The planner can see the free second card but the degraded writer cap
        # still prevents it from overlapping the first startup.
        self.assertEqual(await self.advance(), 'startup_budget_exhausted')
        self.assertEqual(self.writer.effective_startup_limit, 1)
        self.assertEqual(len(self.writes), 1)

    async def test_high_pressure_blocks_otherwise_feasible_parallel_start(self):
        self.parallel(pressure_mode='advisory'); self.rows['workers'][5] = worker(5)
        self.sample.write_text(json.dumps({'observed_at': self.now, 'worker_ids': [4, 5],
                                          'signals': {'network_rx_mib_s': 200}})); self.sample.chmod(0o600)
        self.assertEqual(await self.advance(), 'pressure_high')
        self.assertEqual(self.writes, [])

    async def test_native_default_distributed_object_without_subordinates_is_single_card(self):
        self.parallel()
        self.rows['models'][1].update(replicas=1, ready_replicas=1)
        self.rows['model-instances'][1] = dict(
            id=1, model_id=1, worker_id=4, gpu_indexes=[0], state='running',
            distributed_servers={'mode': 'delegated', 'download_model_files': True,
                                 'subordinate_workers': None})
        self.rows['workers'][4]['status']['gpu_devices'][0]['memory']['allocated'] = 4
        self.state.adopt(self.policy, self.rows['models'], expected_targets={1: 1})
        self.assertEqual(await self.advance(), 'confirmed')
        self.assertTrue(self.observer.view['plan']['parallel_safe'])
        self.assertEqual(self.rows['models'][1]['replicas'], 2)

    async def test_initial_stability_deadline_does_not_wait_full_thirty_second_poll(self):
        self.policy = policy('apply', stability=1)
        self.policy.reconcile = replace(self.policy.reconcile, interval_s=30)
        self.writer.clock = time.monotonic
        queue = asyncio.Queue()
        signals = SimpleNamespace(session=None, changed=asyncio.Event())
        with patch('maas_platform.replica_observe.LOG.info', side_effect=lambda _, p: queue.put_nowait(p)):
            task = asyncio.create_task(run(None, self.listing, signals,
                                           SimpleNamespace(refresh=lambda: self.policy),
                                           writer_factory=lambda _: self.writer))
            try:
                self.assertEqual((await asyncio.wait_for(queue.get(), 4))['state'], 'capacity_stabilizing')
                result = await asyncio.wait_for(queue.get(), 4)
                self.assertEqual(result['execution_state'], 'confirmed')
            finally:
                task.cancel(); await asyncio.gather(task, return_exceptions=True)

    async def test_existing_ready_gpu_without_new_event_can_write_zero_to_one(self):
        self.assertEqual(await self.advance(), 'confirmed')
        self.assertEqual([x['replicas'] for x in self.writes], [1])
        self.assertNotIn('ready_replicas', self.writes[0])
        self.assertEqual(self.state.read()['models']['1']['target'], 1)
        self.assertIsNone(self.journal.read())
        self.assertEqual(await self.advance(), 'idle')
        self.assertEqual(len(self.writes), 1)

    async def test_main_loop_lazy_v2_factory_executes_only_the_adopted_writer(self):
        self.writer.clock = time.monotonic
        queue = asyncio.Queue()
        signals = SimpleNamespace(session=None, changed=asyncio.Event())
        with patch('maas_platform.replica_observe.LOG.info', side_effect=lambda _, p: queue.put_nowait(p)):
            task = asyncio.create_task(run(None, self.listing, signals,
                                           SimpleNamespace(refresh=lambda: self.policy),
                                           writer_factory=lambda _: self.writer))
            try:
                result = await asyncio.wait_for(queue.get(), 5)
                self.assertEqual(result['execution_state'], 'confirmed')
                self.assertTrue(result['write_attempted'])
                self.assertEqual(result['effective_startup_limit'], 1)
                self.assertEqual(len(self.writes), 1)
            finally:
                task.cancel(); await asyncio.gather(task, return_exceptions=True)

    async def test_adoption_pause_and_manual_target_changes_block_new_writes(self):
        self.state.path.unlink()
        self.assertEqual(await self.advance(), 'adoption_required')
        self.state.adopt(self.policy, self.rows['models'], expected_targets={1: 0})
        self.state.pause()
        self.assertEqual(await self.advance(), 'controller_paused')
        self.state.adopt(self.policy, self.rows['models'], expected_targets={1: 0})
        self.state.set_paused(False, pending_guard=self.journal.read)
        self.rows['models'][1]['replicas'] = 1
        self.assertEqual(await self.advance(), 'external_change')
        self.assertEqual(self.writes, [])

    async def test_unknown_response_with_old_target_is_not_retried(self):
        self.outcome = 'before_commit_timeout'
        self.assertEqual(await self.advance(), 'unresolved')
        self.writer = self.make_writer()
        self.assertEqual(await self.advance(), 'unresolved')
        self.assertEqual(len(self.writes), 1)
        self.assertIsNotNone(self.journal.read())

    async def test_lost_success_response_recovers_in_observe_without_resending(self):
        self.outcome = 'after_commit_timeout'
        self.assertEqual(await self.advance(), 'unresolved')
        self.policy = policy('observe', stability=0); self.refresh_value = self.policy
        self.writer = self.make_writer()
        self.assertEqual(await self.advance(), 'confirmed')
        self.assertEqual(len(self.writes), 1)
        self.assertEqual(self.state.read()['models']['1']['target'], 1)
        self.assertIsNone(self.journal.read())

    async def test_confirmation_persistence_failure_keeps_pending_until_recovered(self):
        with patch.object(self.state, 'confirm', side_effect=OSError('disk failure')):
            self.assertEqual(await self.advance(), 'confirmation_unresolved')
        self.assertIsNotNone(self.journal.read())
        self.assertEqual(await self.advance(), 'confirmed')
        self.assertEqual(len(self.writes), 1)

    async def test_required_missing_pressure_still_freezes(self):
        self.policy = policy('apply', pressure_mode='required', stability=0)
        self.refresh_value = self.policy
        self.assertEqual(await self.advance(), 'pressure_unavailable')
        self.assertFalse(self.writer.write_attempted)
        self.assertEqual(self.writes, [])

    async def test_advisory_grace_survives_process_rebuild_and_then_cools_down(self):
        self.policy = policy('apply', pressure_mode='advisory', stability=0)
        self.refresh_value = self.policy
        self.assertEqual(await self.advance(), 'pressure_unknown_grace')
        self.now = 131; self.writer = self.make_writer()
        self.assertEqual(await self.advance(), 'confirmed')
        self.running(); self.now = 140
        self.assertEqual(await self.advance(), 'degraded_write_cooldown')
        self.now = 192
        self.assertEqual(await self.advance(), 'confirmed')
        self.assertEqual([row['replicas'] for row in self.writes], [1, 2])

    async def test_explicit_first_replica_fastpath_does_not_need_missing_data_grace(self):
        self.policy = policy('apply', pressure_mode='advisory', stability=0)
        self.policy.startup = replace(self.policy.startup, first_replica_fastpath=True)
        self.refresh_value = self.policy
        self.assertEqual(await self.advance(), 'confirmed')
        self.assertEqual(len(self.writes), 1)

    async def test_healthy_high_pressure_never_uses_unknown_fallback(self):
        self.policy = policy('apply', pressure_mode='advisory', stability=0)
        self.refresh_value = self.policy
        for stamp in (100, 200):
            self.now = stamp
            self.sample.write_text(json.dumps({'observed_at': stamp, 'worker_ids': [4],
                                               'signals': {'network_rx_mib_s': 200}})); self.sample.chmod(0o600)
            self.assertEqual(await self.advance(), 'pressure_high')
        self.assertEqual(self.writes, [])

    async def test_policy_changed_before_transport_clears_unsent_journal(self):
        self.invalidate_on_get = True
        self.assertEqual(await self.advance(), 'policy_changed')
        self.assertEqual(self.writes, [])
        self.assertIsNone(self.journal.read())

    async def test_operator_pause_before_transport_prevents_native_update(self):
        self.pause_on_refresh = True
        self.assertEqual(await self.advance(), 'control_ownership_changed')
        self.assertEqual(self.writes, [])
        self.assertIsNone(self.journal.read())

    async def test_unknown_write_on_unapproved_runtime_keeps_journal(self):
        self.outcome = 'before_commit_timeout'
        self.assertEqual(await self.advance(), 'unresolved')
        self.version = 'v2.3.0'
        with self.assertRaises(ValueError):
            await self.advance()
        self.assertIsNotNone(self.journal.read())
        self.assertEqual(len(self.writes), 1)

    async def test_expired_snapshot_and_wrong_authorization_do_not_write(self):
        await self.observer.tick(None, self.listing, self.policy, subscribed=False)
        self.now += 31
        self.assertEqual(await self.writer.reconcile(self.policy, self.observer,
                                                    refresh=lambda: self.policy), 'snapshot_expired')
        self.assertEqual(self.writes, [])
        self.writer.model_caps = {1: 1}
        self.assertEqual(await self.advance(), 'deployment_scope_not_authorized')

    async def test_scheduler_filled_fields_do_not_look_like_manual_configuration_change(self):
        self.outcome = 'native_filled_fields'
        self.assertEqual(await self.advance(), 'confirmed')
        self.assertEqual(self.state.check(self.policy, self.rows['models']), 'adopted')

    async def test_operator_adoption_is_explicit_and_never_updates_native_models(self):
        self.state.path.unlink()
        with self.assertRaises(ValueError):
            await control('adopt', self.writer, self.policy, {1: 1}, refresh=lambda: self.policy)
        result = await control('adopt', self.writer, self.policy, {1: 0}, refresh=lambda: self.policy)
        self.assertFalse(result['native_models_updated'])
        self.assertEqual(result['adopted_targets'], {'1': 0})
        result = await control('pause', self.writer, self.policy, {}, refresh=lambda: self.policy)
        self.assertTrue(result['paused'])
        self.assertEqual(self.writes, [])

    async def test_operator_cannot_adopt_while_unknown_write_exists(self):
        self.outcome = 'before_commit_timeout'
        self.assertEqual(await self.advance(), 'unresolved')
        with self.assertRaises(ValueError):
            await control('adopt', self.writer, self.policy, {1: 0}, refresh=lambda: self.policy)
        self.assertIsNotNone(self.journal.read())

    def test_operator_target_parser_rejects_duplicates_and_invalid_ids(self):
        self.assertEqual(targets(['1=0', '2=3']), {1: 0, 2: 3})
        for values in (['1=0', '1=1'], ['0=1'], ['1=-1'], ['1=1=2']):
            with self.assertRaises(ValueError):
                targets(values)

    def test_disabled_capable_environment_does_not_require_a_pressure_feed(self):
        key = self.root/'key'; key.write_text('test-private-key'); key.chmod(0o600)
        environment = {
            'MAAS_REPLICA_APPLY_CLUSTER': '1', 'MAAS_REPLICA_APPLY_MODEL_CAPS': '{"1":2}',
            'MAAS_REPLICA_WRITE_JOURNAL': str(self.root/'write.json'),
            'MAAS_REPLICA_MANAGEMENT_URL': 'http://127.0.0.1:18080',
            'MAAS_REPLICA_MANAGEMENT_KEY_FILE': str(key),
        }
        with patch.dict(os.environ, environment, clear=True):
            writer = ReconcileWriter.from_environment()
        self.assertIsNone(writer.pressure.path)
        self.assertEqual(writer.model_caps, {1: 2})

    def test_operator_status_cli_reads_local_state_without_a_native_api_call(self):
        config = self.root/'policy.json'
        # Keep the full contract fixture in sync with the CLI parser, not a mock Policy object.
        config.write_text(json.dumps({
            'schema_version': 2, 'revision': 1,
            'scope': {'cluster_id': 1, 'owner_principal_id': 1},
            'models': {'1': {'vram_claim_bytes': 4, 'max_replicas': 2}},
            'execution': {'mode': 'observe', 'scale_up_batch_max': 1,
                          'max_inflight_global': 1, 'max_inflight_per_cluster': 1},
            'reconcile': {}, 'pressure': {'mode': 'disabled'},
        })); config.chmod(0o600)
        environment = dict(os.environ, MAAS_REPLICA_ENABLE_APPLY='1',
                           MAAS_REPLICA_APPLY_CLUSTER='1', MAAS_REPLICA_APPLY_MODEL_CAPS='{"1":2}',
                           MAAS_REPLICA_WRITE_JOURNAL=str(self.root/'write.json'),
                           MAAS_REPLICA_STATE_DIR=str(self.root/'state'),
                           MAAS_REPLICA_MANAGEMENT_URL='http://127.0.0.1:1',
                           MAAS_REPLICA_MANAGEMENT_KEY_FILE=str(self.root/'not-read-for-status'))
        environment.pop('MAAS_REPLICA_PRESSURE_FILE', None)
        result = subprocess.run([sys.executable, '-m', 'maas_platform.replica_control', 'status',
                                 '--policy', str(config)], env=environment, capture_output=True,
                                text=True, timeout=5)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(json.loads(result.stdout)['read_only'])
        self.assertFalse(json.loads(result.stdout)['native_models_updated'])


if __name__ == '__main__':
    unittest.main()
