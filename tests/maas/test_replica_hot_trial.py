from copy import deepcopy
import unittest

from maas_platform.replica_apply import MODEL_UPDATE_FIELDS, model_settings
from maas_platform.replica_hot_trial import ReplicaOnlyRequest, image_alias, reordered_command
from maas_replica_execution import WriteNotSent


class ReferenceTest(unittest.TestCase):
    def test_image_alias_keeps_registry_port_repository_and_digest(self):
        digest = 'a'*64
        for name in ['registry.example:5000/org/ocr:release', 'registry.example:5000/org/ocr']:
            self.assertEqual(image_alias(name+'@sha256:'+digest, 'hot-b'),
                             'registry.example:5000/org/ocr:hot-b@sha256:'+digest)

    def test_unpinned_or_malformed_image_input_cannot_start_trial(self):
        for value in ['ocr:latest', 'ocr@sha256:short', None]:
            with self.assertRaises(ValueError):
                image_alias(value, 'hot-b')
        with self.assertRaises(ValueError):
            image_alias('ocr@sha256:'+'a'*64, 'bad/tag')

    def test_command_change_reorders_only_the_verified_ocr_arguments(self):
        import shlex
        self.assertEqual(shlex.split(reordered_command('--port {{port}} --model {{model_name}}')),
                         ['--model', '{{model_name}}', '--port', '{{port}}'])
        with self.assertRaises(ValueError):
            reordered_command('--port {{port}} --other unknown')


class TransportTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.row = {key: None for key in MODEL_UPDATE_FIELDS}
        self.row.update(id=4, cluster_id=1, owner_principal_id=1, replicas=1,
                        env={'GPUSTACK_MODEL_VRAM_CLAIM': '8', 'LATEST': 'B'}, backend_version='1.0.1')
        self.puts = []
        self.records = []
        class Native:
            url = 'http://127.0.0.1:12345'
            async def __call__(inner, method, path, body=None):
                if method == 'PUT':
                    self.puts.append(deepcopy(body))
                    self.row.update(body)
                return deepcopy(self.row)
        self.transport = ReplicaOnlyRequest(Native(), 4, 1, 1,
                                            lambda key, value: self.records.append((key, value)))

    async def test_only_replicas_change_and_latest_configuration_is_preserved(self):
        await self.transport('PUT', '/v2/models/4', {**model_settings(self.row), 'replicas': 2})
        self.assertEqual(len(self.puts), 1)
        self.assertEqual(self.puts[0]['env']['LATEST'], 'B')
        self.assertEqual([key for key, _ in self.records], ['replica_dispatch', 'replica_readback'])
        self.assertEqual(self.transport.puts, 1)

    async def test_stale_environment_or_version_is_definitely_not_sent(self):
        for field, value in [('env', {'OLD': 'value'}), ('backend_version', '1.0.0')]:
            body = {**model_settings(self.row), 'replicas': 2, field: value}
            with self.assertRaises(WriteNotSent):
                await self.transport('PUT', '/v2/models/4', body)
        self.assertEqual(self.puts, [])

    async def test_scope_create_delete_and_zero_are_blocked(self):
        for method, path in [('PUT', '/v2/models/1'), ('POST', '/v2/models'),
                             ('DELETE', '/v2/models/4')]:
            with self.assertRaises(WriteNotSent):
                await self.transport(method, path, {})
        for target in [0, 3, True]:
            with self.assertRaises(WriteNotSent):
                await self.transport('PUT', '/v2/models/4', {**model_settings(self.row), 'replicas': target})
        self.assertEqual(self.puts, [])

    async def test_changed_tenant_identity_is_not_authorized_by_matching_model_id(self):
        self.row['owner_principal_id'] = 2
        with self.assertRaises(WriteNotSent):
            await self.transport('PUT', '/v2/models/4', {**model_settings(self.row), 'replicas': 2})
        self.assertEqual(self.puts, [])
