import hashlib
import json
from pathlib import Path
import unittest
from unittest.mock import patch

import test_replica_writer_v2 as fixture
from test_replica_contract import config
from test_replica_reconcile import worker
from test_replica_reconcile import model
from maas_platform.replica_contract import ControlPolicy
from maas_platform.replica_pending import PendingControl
from maas_platform.replica_native_proof import SOURCES


class PendingTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.f = fixture.WriterV2Test(methodName='runTest'); self.f.setUp()
        self.addCleanup(self.f.doCleanups)
        self.f.rows['models'][1]['placement_strategy'] = 'spread'
        self.f.set_running(3, ready=1)
        for row in self.f.rows['model-instances'].values():
            if row['state'] == 'pending':
                row['state_message'] = 'No suitable workers.\nDetails:\nInsufficient resources'
        self.proof = self.f.root/'proof.json'
        self.proof.write_text(json.dumps({'version': 'v2.2.3', 'source_sha256': SOURCES,
                                         'endpoint_sha256': hashlib.sha256(b'').hexdigest(),
                                         'scores': {'status': 100, 'offload': 10, 'placement': 1}}))
        self.proof.chmod(0o600); self.f.writer.native_proof = self.proof

    async def observe_for(self, seconds):
        result = await self.f.advance()
        while seconds:
            step = min(10, seconds); self.f.now += step; seconds -= step
            result = await self.f.advance()
        return result

    async def test_179_no_write_180_contracts_then_finishes_without_new_three_minutes(self):
        self.assertEqual(await self.observe_for(179), 'pending_observation')
        self.assertEqual(self.f.writes, [])
        self.f.now += 1
        self.assertEqual(await self.f.advance(), 'confirmed')
        self.f.now += 1
        self.assertEqual(await self.f.advance(), 'confirmed')
        self.assertEqual([x['replicas'] for x in self.f.writes], [2, 1])
        self.assertEqual(self.f.rows['model-instances'][1]['state'], 'running')
        for _ in range(3):
            self.f.now += 10
            self.assertEqual(await self.f.advance(), 'pending_hold')
        self.assertEqual(len(self.f.writes), 2)

    async def test_missing_native_proof_keeps_unique_healthy_instance_protected(self):
        self.f.writer.native_proof = None
        self.assertEqual(await self.observe_for(180), 'native_scale_down_proof_required')
        self.assertEqual(self.f.writes, [])

    async def test_altered_native_sources_cannot_enable_the_relaxed_guard(self):
        data = json.loads(self.proof.read_text()); data['source_sha256'] = {}
        self.proof.write_text(json.dumps(data))
        self.assertEqual(await self.observe_for(180), 'native_scale_down_proof_required')
        self.assertEqual(self.f.writes, [])

    async def test_pending_resolution_resets_observation(self):
        await self.observe_for(100)
        self.f.set_running(1)
        await self.f.advance()
        self.assertEqual([r['replicas'] for r in self.f.writes], [2])  # Normal growth stays immediate.
        self.f.set_running(3, ready=1)
        for r in self.f.rows['model-instances'].values():
            if r['state'] == 'pending': r['state_message'] = 'No suitable workers.'
        self.f.now += 1
        self.assertEqual(await self.observe_for(179), 'pending_observation')
        self.assertEqual([r['replicas'] for r in self.f.writes], [2])

    async def test_three_models_converge_to_four_ten_fifteen_and_hold_without_rebalancing(self):
        raw = config(mode='apply'); raw['models'] = {
            str(i): {'max_replicas': cap, 'weight': i, 'min_replicas': 1}
            for i, cap in [(1, 10), (2, 20), (3, 20)]}
        raw['reconcile']['stability_s'] = 0
        self.f.policy = ControlPolicy(raw, allow_apply=True); self.f.refresh_value = self.f.policy
        self.f.writer.model_caps = {1: 10, 2: 20, 3: 20}
        self.f.rows['models'] = {}; self.f.rows['model-instances'] = {}; ident = 0; protected = set()
        for key, desired, ready in [(1, 10, 4), (2, 20, 10), (3, 20, 15)]:
            self.f.rows['models'][key] = {**model(), 'id': key, 'replicas': desired,
                                           'ready_replicas': ready, 'placement_strategy': 'spread'}
            for slot in range(desired):
                ident += 1
                row = dict(id=ident, model_id=key, worker_id=None, gpu_indexes=None,
                           state='pending', state_message='No suitable workers.')
                if slot < ready:
                    row.update(state='running', worker_id=4, gpu_indexes=[0], computed_resource_claim={'vram': {'0': 4}})
                    protected.add(ident)
                self.f.rows['model-instances'][ident] = row
        self.f.rows['workers'][4]['status']['gpu_devices'][0]['memory'].update(total=116, allocated=116)
        self.f.state.adopt(self.f.policy, self.f.rows['models'], expected_targets={1: 10, 2: 20, 3: 20})
        self.assertEqual(await self.observe_for(179), 'pending_observation')
        self.assertEqual(self.f.writes, [])
        self.f.now += 1
        self.assertEqual(await self.f.advance(), 'confirmed')
        for _ in range(20):
            self.f.now += 1
            self.assertEqual(await self.f.advance(), 'confirmed')
        self.assertEqual({i: r['replicas'] for i, r in self.f.rows['models'].items()}, {1: 4, 2: 10, 3: 15})
        self.assertEqual(set(self.f.rows['model-instances']), protected)
        for _ in range(5):
            self.f.now += 10
            self.assertEqual(await self.f.advance(), 'pending_hold')
        self.assertEqual(len(self.f.writes), 21)

    async def test_failed_full_read_resets_timer_not_holding_receipt(self):
        await self.observe_for(100)
        original = self.f.listing
        async def fail(*args): raise ConnectionError()
        self.f.listing = fail
        await self.f.advance()
        self.f.listing = original; self.f.now += 1
        self.assertEqual(await self.observe_for(179), 'pending_observation')
        self.assertEqual(self.f.writes, [])

    async def test_observation_gap_and_process_rebuild_do_not_credit_unobserved_time(self):
        await self.observe_for(100)
        self.f.now += 100
        self.assertEqual(await self.f.advance(), 'pending_observation')
        self.f.observer.pending_control = PendingControl(self.f.state, clock=lambda: self.f.now)
        self.assertEqual(await self.observe_for(179), 'pending_observation')
        self.assertEqual(self.f.writes, [])

    async def test_ready_changes_during_observation_use_latest_ready_without_resetting_180(self):
        raw = config(mode='apply'); raw['models']['1']['max_replicas'] = 8
        raw['reconcile']['stability_s'] = 0
        self.f.policy = ControlPolicy(raw, allow_apply=True); self.f.refresh_value = self.f.policy
        self.f.writer.model_caps = {1: 8}; self.f.set_running(5, ready=1)
        for r in self.f.rows['model-instances'].values():
            if r['state'] == 'pending': r['state_message'] = 'No suitable workers.'
        await self.observe_for(170)
        self.f.set_running(5, ready=2)
        for r in self.f.rows['model-instances'].values():
            if r['state'] == 'pending': r['state_message'] = 'No suitable workers.'
        self.f.now += 10
        self.assertEqual(await self.f.advance(), 'confirmed')
        self.assertEqual(self.f.observer.view['plan']['policy_target'][1], 2)

    async def test_floor_two_with_zero_ready_and_no_resource_stops_at_two_pending(self):
        raw = config(mode='apply'); raw['models']['1'].update(max_replicas=8, min_replicas=2)
        raw['reconcile']['stability_s'] = 0
        self.f.policy = ControlPolicy(raw, allow_apply=True); self.f.refresh_value = self.f.policy
        self.f.writer.model_caps = {1: 8}; self.f.set_running(5, ready=0)
        self.f.rows['workers'][4]['state'] = 'not_ready'
        for r in self.f.rows['model-instances'].values(): r['state_message'] = 'No available workers'
        self.assertEqual(await self.observe_for(180), 'confirmed')
        for _ in range(2):
            self.f.now += 1; self.assertEqual(await self.f.advance(), 'confirmed')
        self.assertEqual([x['replicas'] for x in self.f.writes], [4, 3, 2])
        self.f.now += 1
        self.assertEqual(await self.f.advance(), 'pending_hold')
        self.assertEqual(self.f.rows['models'][1]['replicas'], 2)
        self.assertEqual(len(self.f.rows['model-instances']), 2)

    async def test_hold_survives_restart_but_new_real_capacity_releases_it(self):
        await self.observe_for(180); self.f.now += 1; await self.f.advance()
        self.f.observer.pending_control = PendingControl(self.f.state, clock=lambda: self.f.now)
        self.assertEqual(await self.f.advance(), 'pending_hold')
        self.f.rows['workers'][5] = worker(5); self.f.now += 1
        self.assertEqual(await self.f.advance(), 'confirmed')
        self.assertEqual([x['replicas'] for x in self.f.writes], [2, 1, 2])

    async def test_pause_at_deadline_cannot_be_overridden_and_resume_rechecks_inputs(self):
        await self.observe_for(170); self.f.state.pause(); self.f.now += 10
        self.assertEqual(await self.f.advance(), 'controller_paused')
        self.assertEqual(self.f.writes, [])
        self.f.state.set_paused(False, pending_guard=self.f.journal.read)
        self.assertEqual(await self.f.advance(), 'confirmed')

    async def test_unknown_write_is_not_reissued_at_or_after_observation_deadline(self):
        self.f.outcome = 'before_commit_timeout'
        self.assertEqual(await self.observe_for(180), 'unresolved')
        self.f.now += 1
        self.assertEqual(await self.f.advance(), 'unresolved')
        self.assertEqual(len(self.f.writes), 1)
        self.assertIsNotNone(self.f.journal.read())

    async def test_unexpected_loss_of_protected_instance_keeps_confirmation_unresolved(self):
        original = self.f.writer.request
        self.f.outcome = 'defer_scale_down'
        async def request(method, path, body=None):
            result = await original(method, path, body)
            if method == 'PUT':
                del self.f.rows['model-instances'][1]
                self.f.rows['models'][1]['ready_replicas'] = 0
            return result
        self.f.writer.request = request
        self.assertEqual(await self.observe_for(180), 'confirmation_unresolved')
        self.assertIsNotNone(self.f.journal.read())
        self.f.now += 1
        self.assertEqual(await self.f.advance(), 'confirmation_unresolved')
        self.assertEqual(len(self.f.writes), 1)

    def test_v2_zero_minimums_are_rejected_but_v1_stays_compatible(self):
        for field in ('min_replicas', 'auto_scale_down_min_replicas', 'max_replicas'):
            raw = config(); raw['models']['1'][field] = 0
            with self.assertRaises(ValueError): ControlPolicy(raw)
