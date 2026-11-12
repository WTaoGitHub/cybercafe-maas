import asyncio
import json
import unittest

import httpx

from maas_platform.replica_apply import MODEL_UPDATE_FIELDS, model_settings
from maas_platform.replica_fault_proxy import FaultGate, application, loopback
from maas_platform.gpu_daily_watch import listing


class WaitingStream(httpx.AsyncByteStream):
    def __init__(self):
        self.closed = False

    async def __aiter__(self):
        yield b'{"type":1,"data":{"id":2}}\n'
        await asyncio.Event().wait()

    async def aclose(self):
        self.closed = True


class ProxyTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.row = {key: None for key in MODEL_UPDATE_FIELDS}
        self.row.update(id=2, replicas=0, ready_replicas=0, name='trial', env={})
        self.requests = []
        self.stream = WaitingStream()

        async def native(request):
            self.requests.append(request)
            if request.url.params.get('watch') == 'true':
                return httpx.Response(200, stream=self.stream)
            if request.method == 'PUT':
                self.row['replicas'] = json.loads(request.content)['replicas']
            if request.url.path == '/v2/workers':
                return httpx.Response(200, json={'items': [{'id': 8}],
                                      'pagination': {'page': 1, 'totalPage': 1, 'total': 1}})
            return httpx.Response(200, json=self.row)

        self.upstream = httpx.AsyncClient(base_url='http://127.0.0.1:12345',
                                         transport=httpx.MockTransport(native))
        self.gate = FaultGate()
        self.app = application(self.upstream, 'private-test-key', 2, self.gate, response_delay_s=.01)
        self.client = httpx.AsyncClient(base_url='http://trial',
                       headers={'Authorization': 'Bearer private-test-key'},
                       transport=httpx.ASGITransport(app=self.app))

    async def asyncTearDown(self):
        await self.client.aclose()
        await self.upstream.aclose()

    def body(self):
        return {**model_settings(self.row), 'replicas': 1}

    async def test_other_deployment_and_non_model_mutations_never_reach_native(self):
        for method, path in [('PUT', '/v2/models/1'), ('DELETE', '/v2/models/2'),
                             ('POST', '/v2/models'), ('PUT', '/v2/workers'),
                             ('GET', '/v2/api-keys'), ('PUT', '/v2/models/2?extra=true')]:
            self.assertEqual((await self.client.request(method, path, json={})).status_code, 403)
        self.assertEqual(self.requests, [])

    async def test_only_exact_replica_growth_to_one_can_be_forwarded(self):
        for body in [{**self.body(), 'env': {'OTHER': 'value'}},
                     {**self.body(), 'replicas': 2}, {**self.body(), 'replicas': True}]:
            self.assertEqual((await self.client.put('/v2/models/2', json=body)).status_code, 403)
        self.assertEqual(self.gate.forwarded_puts, 0)
        self.assertEqual((await self.client.put('/v2/models/2', json=self.body())).status_code, 200)
        self.assertEqual(self.row['replicas'], 1)
        self.assertEqual(self.gate.forwarded_puts, 1)

    async def test_auth_failure_has_no_upstream_access(self):
        response = await self.client.get('/v2/models/2', headers={'Authorization': 'Bearer wrong'})
        self.assertEqual(response.status_code, 401)
        self.assertEqual(self.requests, [])

    async def test_after_commit_reply_failure_blocks_confirmation_and_never_retries(self):
        self.gate.change('after_commit')
        self.assertEqual((await self.client.put('/v2/models/2', json=self.body())).status_code, 504)
        self.assertEqual((await self.client.get('/v2/models/2')).status_code, 503)
        self.assertEqual(self.row['replicas'], 1)
        self.gate.change('normal')
        self.assertEqual((await self.client.get('/v2/models/2')).json()['replicas'], 1)
        self.assertEqual(self.gate.forwarded_puts, 1)
        self.assertEqual(len([r for r in self.requests if r.method == 'PUT']), 1)

    async def test_before_commit_fault_does_not_mutate_native_after_clearing(self):
        self.gate.change('before_commit')
        self.assertEqual((await self.client.put('/v2/models/2', json=self.body())).status_code, 504)
        self.gate.change('normal')
        self.assertEqual((await self.client.get('/v2/models/2')).json()['replicas'], 0)
        self.assertEqual(self.gate.forwarded_puts, 0)

    async def test_partial_listing_is_rejected_by_production_listing(self):
        self.gate.change('incomplete_lists')
        with self.assertRaisesRegex(ValueError, 'List changed'):
            await listing(self.client, '/v2/workers')
        self.gate.change('normal')
        self.assertEqual(set(await listing(self.client, '/v2/workers')), {8})

    async def test_read_fault_is_explicit_and_clearable(self):
        self.gate.change('reads_down')
        self.assertEqual((await self.client.get('/v2/workers')).status_code, 503)
        self.assertEqual(self.requests, [])
        self.gate.change('normal')
        self.assertEqual((await self.client.get('/v2/workers')).status_code, 200)

    async def test_existing_subscription_is_cut_and_reconnect_is_rejected(self):
        pending = asyncio.create_task(self.client.get('/v2/workers?watch=true'))
        for _ in range(100):
            if self.gate.watch_connections:
                break
            await asyncio.sleep(.001)
        self.assertEqual(self.gate.watch_connections, 1)
        self.gate.change('watch_down')
        response = await asyncio.wait_for(pending, 1)
        self.assertEqual(response.status_code, 200)
        self.assertTrue(self.stream.closed)
        self.assertEqual((await self.client.get('/v2/workers?watch=true')).status_code, 503)

    def test_remote_or_ambiguous_upstreams_are_not_allowed(self):
        for url in ['http://example.test:80', 'https://127.0.0.1:12',
                    'http://localhost:12', 'http://127.0.0.1:12/v2',
                    'http://user@127.0.0.1:12', 'http://127.0.0.1:12?token=value']:
            with self.assertRaises(ValueError):
                loopback(url)
        self.assertEqual(loopback('http://127.0.0.1:123/'), 'http://127.0.0.1:123')
