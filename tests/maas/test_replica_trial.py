import asyncio
from copy import deepcopy
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from maas_platform.replica_trial import PilotRequest, exercise, inventory, pilot_policy
from maas_replica_execution import PendingWrite
from test_replica_reconcile import model, worker


class PilotTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.directory = Path(self.tmp.name)/'pilot'
        self.models = {1: {**model(), 'replicas': 1, 'ready_replicas': 1}, 2: {**model(), 'id': 2}}
        self.instances = {1: dict(id=1, model_id=1, worker_id=4, gpu_indexes=[0], state='running')}
        self.puts = []; self.lose_response = False

    async def request(self, method, path, body=None):
        if path == '/version':return {'version': 'v2.2.3'}
        if '?' in path:
            kind = path.split('?')[0].rsplit('/', 1)[1]
            values = {'models': list(self.models.values()), 'workers': [worker(4), worker(5)],
                      'model-instances': list(self.instances.values())}[kind]
            return {'items': deepcopy(values), 'pagination': {'page': 1, 'totalPage': 1, 'total': len(values)}}
        ident = int(path.rsplit('/', 1)[1])
        if method == 'GET':return deepcopy(self.models[ident])
        self.assertEqual(method, 'PUT'); self.assertEqual(ident, 2)
        self.puts.append(body)
        if self.lose_response:raise TimeoutError()
        self.models[2].update(replicas=body['replicas'], ready_replicas=body['replicas'])
        self.instances[2] = dict(id=2, model_id=2, worker_id=5, gpu_indexes=[0], state='running')

    async def run_pilot(self):
        original = pilot_policy
        with patch('maas_platform.replica_trial.pilot_policy',
                   side_effect=lambda *a, **kw: original(*a, **kw, stability=0)):
            return await exercise(self.request, self.directory, model_id=2, protected_ids=[1])

    async def test_pilot_only_grows_isolated_model_and_keeps_private_evidence(self):
        result = await self.run_pilot()
        self.assertEqual(result['phase'], 'NATIVE_READY')
        self.assertEqual(result['native_write_attempts'], 1)
        self.assertEqual(self.models[1]['replicas'], 1)
        self.assertEqual(self.directory.stat().st_mode & 0o777, 0o700)
        self.assertEqual((self.directory/'trace.jsonl').stat().st_mode & 0o777, 0o600)

    async def test_unknown_result_retains_journal_and_existing_directory_cannot_restart(self):
        self.lose_response = True
        with self.assertRaises(ValueError):await self.run_pilot()
        self.assertIsNotNone(PendingWrite(self.directory/'write.json').read())
        self.assertEqual(json.loads((self.directory/'status.json').read_text())['phase'],
                         'STOPPED_REQUIRES_INSPECTION')
        with self.assertRaises(FileExistsError):await self.run_pilot()
        self.assertEqual(len(self.puts), 1)

    async def test_protected_model_drift_stops_before_transport(self):
        calls = 0
        async def changed(method, path, body=None):
            nonlocal calls
            if path.startswith('/v2/models?'):
                calls += 1
                if calls >= 2:self.models[1]['replicas'] = 2
            return await self.request(method, path, body)
        with self.assertRaises(ValueError):
            await exercise(changed, self.directory, model_id=2, protected_ids=[1])
        self.assertEqual(self.puts, [])

    async def test_nonzero_target_or_existing_instance_refuses_adoption(self):
        self.models[2]['replicas'] = 1
        with self.assertRaises(ValueError):await self.run_pilot()
        self.assertEqual(self.puts, [])

    async def test_transport_refuses_cross_model_scale_down_and_unread_target(self):
        transport = PilotRequest(self.request, 2)
        for method, path, body in [('PUT','/v2/models/2',{'replicas':1}),
                                   ('DELETE','/v2/models/2',None),
                                   ('PUT','/v2/models/1',{'replicas':2})]:
            with self.assertRaises(ValueError):await transport(method,path,body)
        await transport('GET','/v2/models/2')
        for target in (0,2,-1):
            with self.assertRaises(ValueError):await transport('PUT','/v2/models/2',{'replicas':target})
        self.assertEqual(self.puts, [])

    async def test_duplicate_or_incomplete_paging_is_refused(self):
        async def duplicate(*args):
            return {'items':[{'id':1},{'id':1}]}
        with self.assertRaises(ValueError):await inventory(duplicate,'models')
        async def partial(*args):
            return {'items':[{'id':1}], 'pagination':{'page':1,'totalPage':1,'total':2}}
        with self.assertRaises(ValueError):await inventory(partial,'models')

    def test_policy_has_explicit_scope_and_never_enlarges_pilot_bounds(self):
        for maximum, limit in [(5,1),(1,3),(True,1)]:
            with self.assertRaises(ValueError):
                pilot_policy(self.models[2], maximum=maximum, limit=limit,
                             pressure_mode='disabled', parallel=False)
        raw=pilot_policy(self.models[2], maximum=4, limit=2,pressure_mode='advisory',parallel=True)
        self.assertEqual(list(raw['models']), ['2'])
        self.assertEqual(raw['pressure']['mode'], 'advisory')
        self.assertNotIn('first_replica_fastpath',raw['startup'])


if __name__ == '__main__':unittest.main()
