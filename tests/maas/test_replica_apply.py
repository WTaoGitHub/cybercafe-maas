from copy import deepcopy
from datetime import datetime, timezone
from importlib.util import find_spec
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest

if find_spec('maas_replica_apply') is None:
    raise unittest.SkipTest('Add plugins/gpustack-replica to PYTHONPATH')

from maas_platform.replica_apply import MODEL_UPDATE_FIELDS, deployment_binding, model_settings
from maas_platform.replica_observe import native_devices
from maas_replica_apply import ScaleUp
from maas_replica_execution import PendingWrite
from maas_replica_planner import plan_snapshot
from maas_replica_policy import Policy
from maas_replica_pressure import PressureGate


class ReplicaApplyTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        directory = TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.journal = PendingWrite(self.root / 'pending.json')
        self.sample = self.root / 'pressure.json'
        self.sample.write_text(json.dumps({
            'observed_at': 100, 'worker_ids': [1, 2], 'signals': {'rejections': 0},
        }))
        self.sample.chmod(0o600)
        self.policy = self.make_policy('apply')
        self.model = dict.fromkeys(MODEL_UPDATE_FIELDS)
        self.model.update(id=18, cluster_id=1, owner_principal_id=1, name='ocr',
                          replicas=1, ready_replicas=1, cpu_offloading=False,
                          distributed_inference_across_workers=False,
                          env={'GPUSTACK_MODEL_VRAM_CLAIM': '4'})
        self.instances = [dict(id=1, model_id=18, worker_id=1, gpu_indexes=[0], state='running')]
        self.workers = {
            ident: dict(id=ident, cluster_id=1, state='ready', unreachable=False, labels={},
                        system_reserved={'vram': 0},
                        status={'gpu_devices': [dict(uuid=f'gpu-{ident}', index=0, type='cuda',
                                                     memory={'total': 4, 'allocated': allocated})]})
            for ident, allocated in ((1, 4), (2, 0))
        }
        self.writes = []
        self.outcome = 'success'
        self.clock = 100
        self.regulator = self.new_regulator()

    @staticmethod
    def make_policy(mode):
        return Policy({
            'schema_version': 1, 'revision': 1, 'scope': {'cluster_id': 1, 'owner_principal_id': 1},
            'models': {'18': {'max_replicas': 2, 'vram_claim_bytes': 4}},
            'execution': {'mode': mode, 'max_inflight_global': 1, 'max_inflight_per_cluster': 1},
            'pressure': {'max_sample_age_s': 10, 'recovery_hold_s': 0,
                         'signals': {'rejections': {'low': 0, 'high': 1}}},
        }, allow_apply=True)

    def new_regulator(self):
        journal = PendingWrite(self.journal.path)
        return ScaleUp(1, journal, self.request, pressure=PressureGate(self.sample, clock=lambda: self.clock),
                       model_caps={18: 2}, serializer=model_settings)

    async def request(self, method, path, body=None):
        path = path.split('?')[0]
        if path == '/version':
            return {'version': 'v2.2.3'}
        inventory = {'/v2/workers': list(self.workers.values()), '/v2/models': [self.model],
                     '/v2/model-instances': self.instances}
        if path in inventory:
            items = deepcopy(inventory[path])
            return {'items': items, 'pagination': {'page': 1, 'totalPage': 1, 'total': len(items)}}
        self.assertEqual(path, '/v2/models/18')
        if method == 'GET':
            return deepcopy(self.model)
        self.assertIsNotNone(self.journal.read())
        self.writes.append(body)
        if self.outcome == 'lost_before_commit':
            raise TimeoutError()
        self.model['replicas'] = body['replicas']
        self.instances.append(dict(id=2, model_id=18, worker_id=None, gpu_indexes=None, state='pending'))
        if self.outcome == 'lost_after_commit':
            raise TimeoutError()

    async def advance(self, *, arrival_valid=lambda: True):
        devices = await native_devices(1, self.workers)
        event = 'gpu_added'
        plan = plan_snapshot(self.policy, event, devices, [SimpleNamespace(**self.model)],
                             [SimpleNamespace(**row) for row in self.instances])
        return await self.regulator.advance(
            self.policy, event, devices, plan, refresh=lambda: self.policy,
            bindings={18: deployment_binding(self.model)}, arrival_valid=arrival_valid,
        )

    async def test_applies_one_replica_without_overwriting_readiness(self):
        expected = {**model_settings(self.model), 'replicas': 2}
        self.assertEqual(await self.advance(), 'confirmed')
        self.assertEqual(self.writes, [expected])
        self.assertNotIn('ready_replicas', self.writes[0])
        self.assertIsNone(self.journal.read())
        self.assertEqual(await self.advance(), 'idle')
        self.assertEqual(len(self.writes), 1)

    async def test_expired_pressure_and_arrival_never_write(self):
        self.clock = 111
        self.assertEqual(await self.advance(), 'pressure_unavailable')
        self.clock = 100
        self.assertEqual(await self.advance(arrival_valid=lambda: False), 'arrival_expired')
        self.assertEqual(self.writes, [])
        self.assertIsNone(self.journal.read())

    async def test_lost_response_recovery_in_observe_does_not_resend(self):
        self.outcome = 'lost_after_commit'
        self.assertEqual(await self.advance(), 'unresolved')
        self.policy = self.make_policy('observe')
        self.regulator = self.new_regulator()
        self.assertEqual(await self.advance(), 'confirmed')
        self.assertEqual(len(self.writes), 1)
        self.assertIsNone(self.journal.read())

    async def test_old_target_after_timeout_does_not_prove_safe_retry(self):
        self.outcome = 'lost_before_commit'
        self.assertEqual(await self.advance(), 'unresolved')
        self.regulator = self.new_regulator()
        self.assertEqual(await self.advance(), 'unresolved')
        self.assertEqual(len(self.writes), 1)
        self.assertEqual(self.journal.read().after, 2)

    def test_missing_schema_field_and_lora_are_rejected(self):
        with self.assertRaisesRegex(ValueError, 'schema'):
            deployment_binding({key: val for key, val in self.model.items() if key != 'env'})
        with self.assertRaisesRegex(ValueError, 'LoRA'):
            deployment_binding({**self.model, 'lora_list': [{'lora_name': 'short'}]})

    @unittest.skipUnless(find_spec('gpustack'), 'Needs the pinned GPUStack runtime')
    def test_pinned_gpu_stack_accepts_public_ocr_settings(self):
        from gpustack.schemas.models import ModelPublic, ModelUpdate

        now = datetime.now(timezone.utc)
        row = ModelPublic(id=18, name='ocr', cluster_id=1, owner_principal_id=1,
                          source='local_path', local_path='/ocr', backend='custom',
                          image_name='ocr:test', run_command='serve', replicas=1, ready_replicas=1,
                          created_at=now, updated_at=now).model_dump(mode='json')
        self.assertEqual(set(ModelUpdate.model_fields), MODEL_UPDATE_FIELDS)
        payload = {**model_settings(row), 'replicas': 2}
        validated = ModelUpdate.model_validate(payload)
        self.assertEqual(validated.model_dump(mode='json', exclude_unset=True), payload)
