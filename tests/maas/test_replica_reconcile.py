import asyncio
from copy import deepcopy
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

from maas_platform.replica_apply import MODEL_UPDATE_FIELDS, deployment_binding
from maas_platform.replica_contract import ControlPolicy
from maas_platform.replica_observe import native_devices, run, _log_identity
from maas_platform.replica_reconcile import ReconcileObserve
from maas_platform.replica_state import ControlState
from maas_replica_execution import Action, PendingWrite
from maas_replica_native import binding
from maas_platform.replica_apply import model_settings
from maas_replica_pressure import PressureGate


def policy(mode='observe', pressure_mode='disabled', stability=5):
    pressure = {'mode': pressure_mode}
    if pressure_mode != 'disabled':
        pressure.update(max_sample_age_s=20, recovery_hold_s=0, require_worker_coverage=True,
                        signals={'network_rx_mib_s': {'low': 40, 'high': 150}})
    return ControlPolicy({
        'schema_version': 2, 'revision': 1, 'scope': {'cluster_id': 1, 'owner_principal_id': 1},
        'models': {'1': {'vram_claim_bytes': 4, 'max_replicas': 2}},
        'execution': {'mode': mode, 'scale_up_batch_max': 1,
                      'max_inflight_global': 2, 'max_inflight_per_cluster': 2},
        'reconcile': {'interval_s': 1, 'stability_s': stability}, 'pressure': pressure,
    }, allow_apply=True)


def model():
    row = dict.fromkeys(MODEL_UPDATE_FIELDS)
    row.update(id=1, cluster_id=1, owner_principal_id=1, name='ocr', replicas=0, ready_replicas=0,
               cpu_offloading=False, distributed_inference_across_workers=False,
               env={'GPUSTACK_MODEL_VRAM_CLAIM': '4'})
    return row


def worker(ident=4):
    return dict(id=ident, cluster_id=1, state='ready', unreachable=False, labels={},
                maintenance=None, system_reserved={'vram': 0},
                status={'gpu_devices': [dict(index=0, uuid=f'gpu-{ident}', type='cuda',
                                             memory={'total': 8, 'allocated': 0})]})


class ReconcileTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name); self.now = 100
        self.rows = {'workers': {4: worker()}, 'models': {1: model()}, 'model-instances': {}}
        self.fail = False
        self.observe = ReconcileObserve(native_devices, PressureGate(None),
                                        state_directory=self.root / 'state',
                                        clock=lambda: self.now, wall_clock=lambda: self.now)

    async def listing(self, client, kind):
        if self.fail:
            raise ConnectionError('private source detail must not be logged')
        return deepcopy(self.rows[kind])

    async def tick(self, config=None, **kw):
        return await self.observe.tick(None, self.listing, config or policy(), subscribed=False, **kw)

    async def test_periodic_startup_with_ready_gpu_does_not_need_arrival_event(self):
        first = await self.tick()
        self.assertEqual(first['state'], 'capacity_stabilizing')
        self.now = 106
        after = await self.tick()
        self.assertEqual(after['state'], 'observe_ready')
        self.assertEqual(after['policy_target'], {1: 2})
        self.assertEqual(after['trigger'], 'current_state_reconcile')
        self.assertEqual(after['subscription_state'], 'polling_fallback')
        self.assertFalse(after['writes_enabled'])
        self.assertIsNone(after['event'])
        self.assertEqual(json.loads((self.root/'state/status.json').read_text())['state'], 'observe_ready')
        self.now += 1000
        self.assertEqual((await self.tick())['policy_target'], {1: 2})

    async def test_partial_api_failure_resets_stability_but_not_to_cached_capacity(self):
        await self.tick(); self.now += 6
        self.assertEqual((await self.tick())['state'], 'observe_ready')
        self.fail = True; failed = await self.tick()
        self.assertEqual(failed['state'], 'blocked')
        self.assertNotIn('private source detail', json.dumps(failed))
        self.fail = False; self.now += 100
        self.assertEqual((await self.tick())['state'], 'capacity_stabilizing')
        self.now += 6
        self.assertEqual((await self.tick())['state'], 'observe_ready')

    async def test_heartbeat_metadata_does_not_reset_capacity_stability(self):
        await self.tick()
        self.now += 6; self.rows['workers'][4]['updated_at'] = 'changed'
        self.assertEqual((await self.tick())['state'], 'observe_ready')
        self.rows['workers'][4]['status']['gpu_devices'][0]['memory']['allocated'] = 4
        self.assertEqual((await self.tick())['state'], 'capacity_stabilizing')

    async def test_failed_partial_read_cancels_remaining_requests(self):
        cancelled = []

        async def partial(client, kind):
            if kind == 'models':
                raise ConnectionError('unavailable')
            try:
                await asyncio.Event().wait()
            finally:
                cancelled.append(kind)

        result = await self.observe.tick(None, partial, policy(), subscribed=True)
        self.assertEqual(result['state'], 'blocked')
        self.assertEqual(sorted(cancelled), ['model-instances', 'workers'])

    async def test_new_current_workers_not_covered_by_retired_worker_sample(self):
        sample = self.root/'pressure.json'
        sample.write_text(json.dumps({'observed_at': 1, 'worker_ids': [3],
                                      'signals': {'network_rx_mib_s': 0}})); sample.chmod(0o600)
        self.observe.pressure = PressureGate(sample, clock=lambda: self.now)
        self.rows['workers'][5] = worker(5)
        result = await self.tick(policy(pressure_mode='required'))
        self.assertEqual(result['eligible_worker_ids'], [4, 5])
        self.assertEqual(result['pressure']['reasons'], ['sample_stale', 'missing_workers'])
        self.assertEqual(result['pressure']['missing_worker_ids'], [4, 5])

    async def test_pending_and_conflicting_inventory_never_gain_write_authority(self):
        self.rows['workers'][5] = deepcopy(self.rows['workers'][4]); self.rows['workers'][5]['id'] = 5
        self.assertEqual((await self.tick())['state'], 'blocked')
        del self.rows['workers'][5]
        self.rows['models'][1]['replicas'] = 1
        self.rows['model-instances'][1] = dict(id=1, model_id=1, state='starting', worker_id=4, gpu_indexes=[0])
        result = await self.tick(policy('apply', stability=0))
        self.assertEqual(result['up_preview_limit'], 0)
        self.assertEqual(result['execution_state'], 'v2_apply_not_enabled')
        self.assertFalse(result['writes_enabled'])

    async def test_main_loop_v2_never_calls_old_arrival_writer(self):
        signals = SimpleNamespace(session=None, changed=asyncio.Event())
        writer = SimpleNamespace(advance=AsyncMock(side_effect=AssertionError('must not write')))
        queue = asyncio.Queue()
        with patch('maas_platform.replica_observe.LOG.info', side_effect=lambda _, p: queue.put_nowait(p)):
            task = asyncio.create_task(run(None, self.listing, signals,
                                           SimpleNamespace(refresh=lambda: policy('apply', stability=0)),
                                           scale_up=writer))
            try:
                result = await asyncio.wait_for(queue.get(), 5)
                self.assertEqual(result['execution_state'], 'v2_apply_not_enabled')
                writer.advance.assert_not_called()
            finally:
                task.cancel(); await asyncio.gather(task, return_exceptions=True)

    def test_age_changes_do_not_generate_duplicate_transition_logs(self):
        self.assertEqual(_log_identity({'observed_at': 1, 'pressure': {'sample_age_s': 2, 'state': 'open'}}),
                         _log_identity({'observed_at': 2, 'pressure': {'sample_age_s': 3, 'state': 'open'}}))


class StateTest(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name); self.now = 100
        self.state = ControlState(self.root/'state', clock=lambda: self.now)
        self.config = policy(); self.models = {1: model()}

    def adopt(self):
        return self.state.adopt(self.config, self.models, expected_targets={1: self.models[1]['replicas']})

    def action(self):
        row = self.models[1]
        return Action(1, 'snapshot', 1, 1, 1, deployment_binding(row), 0, 1,
                      binding(row, recovery=True, serializer=model_settings))

    def test_replica_count_drift_is_rebased_but_configuration_changes_are_fenced(self):
        self.assertEqual(self.state.check(self.config, self.models), 'adoption_required')
        with self.assertRaises(ValueError):
            self.state.adopt(self.config, self.models, expected_targets={1: 1})
        self.adopt()
        self.assertEqual(self.state.check(self.config, self.models), 'adopted')
        self.models[1]['replicas'] = 1
        self.assertEqual(self.state.check(self.config, self.models), 'adopted')
        self.state.rebase_targets(self.config, self.models, pending_guard=lambda: None)
        self.assertEqual(self.state.read()['models']['1']['target'], 1)
        self.assertEqual(self.state.read()['last_external_change']['models']['1'], {'before': 0, 'observed': 1})
        self.models[1]['image_name'] = 'changed-image'
        self.assertEqual(self.state.check(self.config, self.models), 'external_change')
        self.adopt()
        self.state.pause()
        self.assertEqual(self.state.check(self.config, self.models), 'controller_paused')
        self.assertEqual(ControlState(self.root/'state').read()['generation'], 4)

    def test_confirm_is_durable_idempotent_and_preserves_cooldown(self):
        self.adopt(); action = self.action(); after = {**self.models[1], 'replicas': 1}
        self.state.confirm(action, after)
        self.now = 200; self.state.confirm(action, after)
        data = ControlState(self.root/'state').read()
        self.assertEqual(data['last_write_at'], 100)
        self.assertEqual(data['models']['1']['target'], 1)
        self.assertEqual(self.state.check(self.config, {1: after}), 'adopted')
        self.assertEqual((self.root/'state/ownership.json').stat().st_mode & 0o777, 0o600)

    def test_unconfirmed_or_wrong_scope_write_cannot_advance_baseline(self):
        self.adopt(); before = self.state.read(); action = self.action()
        with self.assertRaises(ValueError):
            self.state.confirm(action, {**self.models[1], 'replicas': 2})
        self.assertEqual(self.state.read(), before)
        with self.assertRaises(ValueError):
            self.state.confirm(action, {**self.models[1], 'replicas': 1, 'cluster_id': 2})

    def test_private_permissions_and_malformed_state_fail_closed(self):
        self.adopt(); self.state.path.chmod(0o644)
        with self.assertRaises(ValueError):
            self.state.read()
        self.state.path.chmod(0o600); self.state.path.write_text('[]')
        with self.assertRaises(ValueError):
            self.state.read()
        self.state.directory.chmod(0o755)
        with self.assertRaises(ValueError):
            self.state.read()


if __name__ == '__main__':
    unittest.main()
