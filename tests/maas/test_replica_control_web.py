import asyncio
from dataclasses import replace
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest

from fastapi import FastAPI
from fastapi.testclient import TestClient

from maas_platform.replica_apply import deployment_binding
from maas_platform.replica_control_web import ControlEndpoint, router
from maas_platform.replica_observe import _control_stamp, _wait
from maas_platform.replica_state import ControlState
from maas_replica_execution import Action, PendingWrite
from test_replica_contract import config
from test_replica_reconcile import model
from maas_platform.replica_contract import ControlPolicy


class ControlWebTest(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.key = self.root/'key'; self.key.write_text('test-operator-secret-' + 'x'*32); self.key.chmod(0o600)
        self.path = self.root/'policy.json'
        self.raw = config(mode='apply', pressure_mode='disabled')
        self.path.write_text(json.dumps(self.raw)); self.path.chmod(0o600)
        self.policy = ControlPolicy(self.raw, allow_apply=True)
        self.state = ControlState(self.root/'state')
        self.state.adopt(self.policy, {1: model()}, expected_targets={1: 0})
        self.endpoint = ControlEndpoint(self.path, self.root/'state', self.root/'write.json')
        self.app = FastAPI(); self.app.include_router(router(self.endpoint, key_file=self.key))
        self.client = TestClient(self.app)
        self.headers = {'Authorization': 'Bearer ' + self.key.read_text()}
        self.url = '/maas/ops/replica-regulator'

    def call(self, path='', method='get'):
        return getattr(self.client, method)(self.url+path, headers=self.headers)

    def test_operator_auth_is_distinct_and_disabled_by_default(self):
        self.assertEqual(self.client.get(self.url).status_code, 401)
        self.assertEqual(self.client.post(self.url+'/pause', headers={'Authorization': 'Bearer customer-key'}).status_code, 401)
        app = FastAPI(); app.include_router(router())
        self.assertEqual(TestClient(app).get(self.url).status_code, 404)
        self.key.chmod(0o644)
        self.assertEqual(self.call('/pause', 'post').status_code, 503)

    def test_pause_resume_are_persistent_idempotent_without_native_writes(self):
        first = self.call('/pause', 'post').json()
        self.assertTrue(first['paused']); self.assertFalse(first['native_models_updated'])
        repeated = self.call('/pause', 'post').json()
        self.assertEqual(first['generation'], repeated['generation'])
        self.assertFalse(repeated['state_updated'])
        self.assertTrue(ControlState(self.root/'state').read()['paused'])
        # Adoption is configuration approval, never an implicit resume.
        self.state.adopt(self.policy, {1: model()}, expected_targets={1: 0})
        self.assertTrue(self.call().json()['paused'])
        resumed = self.call('/resume', 'post').json()
        self.assertFalse(resumed['paused']); self.assertTrue(resumed['pause_gate_open'])
        self.assertEqual(resumed['generation'], self.call('/resume', 'post').json()['generation'])
        self.assertTrue(resumed['reconcile_checks_required'])
        self.assertEqual(self.call().headers['cache-control'], 'no-store')

    def test_inflight_dispatch_lock_cannot_falsely_acknowledge_pause(self):
        with self.state.locked():
            result = self.call('/pause', 'post')
        self.assertEqual(result.status_code, 409)
        self.assertEqual(result.json()['detail']['code'], 'CONTROL_BUSY')
        self.assertFalse(self.state.read()['paused'])
        self.assertTrue(self.call('/pause', 'post').json()['paused'])

    def test_pause_allowed_with_pending_but_resume_never_clears_it(self):
        journal = PendingWrite(self.root/'write.json')
        action = Action(1, 'fingerprint', 1, 1, 1, deployment_binding(model()), 0, 1)
        journal.reserve(action)
        paused = self.call('/pause', 'post')
        self.assertEqual(paused.status_code, 200)
        self.assertTrue(paused.json()['pending_write'])
        resumed = self.call('/resume', 'post')
        self.assertEqual(resumed.status_code, 409)
        self.assertEqual(resumed.json()['detail']['code'], 'PENDING_WRITE')
        self.assertEqual(journal.read(), action)
        self.assertTrue(self.state.read()['paused'])

    def test_scope_change_cannot_block_emergency_pause_but_requires_adoption_to_resume(self):
        self.raw['scope']['cluster_id'] = 2; self.raw['revision'] += 1
        self.path.write_text(json.dumps(self.raw))
        self.assertEqual(self.call('/pause', 'post').status_code, 200)
        self.assertEqual(self.call('/resume', 'post').json()['detail']['code'], 'CONTROL_SCOPE_CHANGED')

    def test_swagger_has_all_three_operations_and_dedicated_security(self):
        spec = self.app.openapi()
        self.assertEqual(len(spec['paths']), 3)
        for path in spec['paths'].values():
            operation = next(iter(path.values()))
            self.assertEqual(operation['security'], [{'ReplicaOperatorBearer': []}])
            self.assertTrue(operation['description'])
            self.assertNotIn('requestBody', operation)
        self.assertIn('paused', spec['components']['schemas']['ControlResponse']['properties'])


class WakeTest(unittest.IsolatedAsyncioTestCase):
    async def test_cross_process_gate_change_wakes_without_native_event(self):
        with TemporaryDirectory() as directory:
            state = ControlState(Path(directory)/'state')
            raw = config(); policy = ControlPolicy(raw)
            state.adopt(policy, {1: model()}, expected_targets={1: 0})
            loader = SimpleNamespace(path=None); observer = SimpleNamespace(state=state)
            stamp = _control_stamp(loader, observer)
            waiting = asyncio.create_task(_wait(asyncio.Event(), 30, loader, observer, stamp))
            await asyncio.sleep(0)
            state.pause()
            await asyncio.wait_for(waiting, 2)
