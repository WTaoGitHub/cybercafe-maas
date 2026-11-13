from copy import deepcopy
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from maas_replica_execution import WriteNotSent
from maas_platform.replica_apply import MODEL_UPDATE_FIELDS, model_settings
from maas_platform.replica_cold_trial import (CLAIM, ColdRequest, WarmRequest, policy,
                                            validate_proof, validate_warm_proof)
from maas_platform.replica_state import atomic_private


def spec():
    return {'test_id': 'cold-test', 'model_id': 9, 'cluster_id': 1, 'owner_id': 1,
            'image_digest': 'registry.invalid/ocr@sha256:'+'a'*64,
            'protected_worker_ids': [4, 5], 'protected_gpu_uuids': ['GPU-existing']}


def proof():
    return {'test_id': 'cold-test', 'model_id': 9, 'worker_id': 6,
            'gpu_uuid': 'GPU-new', 'boot_id': '00000000-0000-0000-0000-000000000001',
            'image_digest': spec()['image_digest'], 'image_present': False,
            'target_model_files_present': False, 'source': 'authenticated_ssh',
            'observed_controller_at': 100, 'inventory_sha256': 'b'*64}


class ProofTest(unittest.TestCase):
    def test_preparation_policy_never_enables_apply(self):
        raw = policy(9, 1, 1)
        self.assertEqual(raw['execution']['mode'], 'observe')
        self.assertEqual(raw['models'], {'9': {'min_replicas': 1, 'max_replicas': 1}})
        self.assertEqual(raw['execution']['max_inflight_global'], 1)

    def test_scope_cached_stale_future_and_existing_gpu_refused(self):
        for change in [{'test_id': 'another'}, {'worker_id': 4}, {'gpu_uuid': 'GPU-existing'},
                       {'image_present': True}, {'image_present': 0},
                       {'target_model_files_present': True}, {'source': 'manual-guess'},
                       {'observed_controller_at': -500}, {'observed_controller_at': 201},
                       {'image_digest': 'another'}, {'boot_id': ''}]:
            with self.assertRaises(ValueError):
                validate_proof({**proof(), **change}, spec(), now=200)
        self.assertEqual(validate_proof(proof(), spec(), now=200)['worker_id'], 6)


class GateTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.row = {k: None for k in MODEL_UPDATE_FIELDS}
        self.row.update(id=9, cluster_id=1, owner_principal_id=1, replicas=0, ready_replicas=0,
                        worker_selector={'worker-name': 'GPU-new'}, backend='test', backend_version='1',
                        env={'GPUSTACK_MODEL_VRAM_CLAIM': str(CLAIM)})
        self.worker = {'id': 6, 'name': 'GPU-new', 'state': 'ready', 'cluster_id': 1,
                       'maintenance': {'enabled': False}, 'labels': {'worker-name': 'GPU-new'},
                       'status': {'gpu_devices': [{'uuid': 'GPU-new'}]}}
        self.image = spec()['image_digest']; self.puts = []; self.fail = False
        parent = self
        class Native:
            url = 'http://127.0.0.1:12345'
            async def __call__(self, method, path, body=None):
                if method == 'PUT':
                    parent.puts.append(deepcopy(body))
                    if parent.fail:raise TimeoutError('uncertain')
                    return {**parent.row, **body}
                if 'workers/' in path:return deepcopy(parent.worker)
                if 'inference-backends?' in path:
                    return {'items': [{'id': 12, 'backend_name': 'test',
                            'version_configs': {'1': {'image_name': parent.image}}}]}
                return deepcopy(parent.row)
        self.native = Native()

    def request(self, allow=True):
        return ColdRequest(self.native, spec(), self.root, allow_start=allow, clock=lambda: 200)

    async def put(self, request, **extra):
        return await request('PUT', '/v2/models/9', {**model_settings(self.row), 'replicas': 1, **extra})

    async def test_observe_refuses_every_mutation_even_with_valid_proof(self):
        atomic_private(self.root/'cold-proof.json', proof())
        with self.assertRaises(WriteNotSent):await self.put(self.request(False))
        self.assertEqual(self.puts, [])
        self.assertFalse((self.root/'one-shot-intent.json').exists())

    async def test_warm_requires_cached_same_boot_success_and_separate_intent(self):
        cold = {'worker_id': 6, 'gpu_uuid': 'GPU-new', 'boot_id': proof()['boot_id'],
                'result_sha256': 'c'*64}
        scope = {**spec(), 'successful_cold': cold}
        warm = {**proof(), 'cache_phase': 'warm', 'image_present': True,
                'successful_cold_sha256': 'c'*64}
        with self.assertRaises(ValueError):validate_proof(warm, scope, now=200)
        for change in [{'boot_id': '00000000-0000-0000-0000-000000000002'},
                       {'successful_cold_sha256': 'd'*64}, {'image_present': False},
                       {'cache_phase': 'cold'}, {'observed_controller_at': -500}]:
            with self.assertRaises(ValueError):
                validate_warm_proof({**warm, **change}, scope, now=200)
        atomic_private(self.root/'warm-proof.json', warm)
        request = WarmRequest(self.native, scope, self.root, allow_start=True, clock=lambda: 200)
        await self.put(request)
        with self.assertRaises(WriteNotSent):await self.put(request)
        self.assertEqual(len(self.puts), 1)

    async def test_warm_observe_never_releases_even_with_successful_cached_proof(self):
        warm = {**proof(), 'cache_phase': 'warm', 'image_present': True,
                'successful_cold_sha256': 'c'*64}
        scope = {**spec(), 'successful_cold': {'worker_id': 6, 'gpu_uuid': 'GPU-new',
                    'boot_id': proof()['boot_id'], 'result_sha256': 'c'*64}}
        atomic_private(self.root/'warm-proof.json', warm)
        with self.assertRaises(WriteNotSent):
            await self.put(WarmRequest(self.native, scope, self.root, clock=lambda: 200))
        self.assertEqual(self.puts, [])
        self.assertFalse((self.root/'one-shot-intent.json').exists())

    async def test_missing_evidence_and_protected_model_refused(self):
        with self.assertRaises(WriteNotSent):await self.put(self.request())
        with self.assertRaises(WriteNotSent):await self.request()('PUT', '/v2/models/1', {})
        self.assertEqual(self.puts, [])

    async def test_exact_single_update_and_persistent_second_attempt_refusal(self):
        atomic_private(self.root/'cold-proof.json', proof())
        await self.put(self.request())
        with self.assertRaises(WriteNotSent):await self.put(self.request())
        self.assertEqual(len(self.puts), 1)
        self.assertEqual(json.loads((self.root/'one-shot-intent.json').read_text())['after'], 1)

    async def test_uncertain_transport_keeps_latch_and_never_replays(self):
        atomic_private(self.root/'cold-proof.json', proof()); self.fail = True
        with self.assertRaises(TimeoutError):await self.put(self.request())
        with self.assertRaises(WriteNotSent):await self.put(self.request())
        self.assertEqual(len(self.puts), 1)

    async def test_changed_gpu_selector_image_and_non_replica_config_refused(self):
        atomic_private(self.root/'cold-proof.json', proof())
        original = deepcopy(self.worker)
        self.worker['state'] = 'not_ready'
        with self.assertRaises(WriteNotSent):await self.put(self.request())
        self.worker = original; self.image = 'changed'
        with self.assertRaises(WriteNotSent):await self.put(self.request())
        self.image = spec()['image_digest']
        with self.assertRaises(WriteNotSent):await self.put(self.request(), env={})
        with self.assertRaises(WriteNotSent):await self.put(self.request(), replicas=True)
        self.assertEqual(self.puts, [])
