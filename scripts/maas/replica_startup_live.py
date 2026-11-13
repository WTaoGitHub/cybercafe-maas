"""Finite prewarmed startup comparison, explicit three-model scope, one writer."""
import argparse
import asyncio
import base64
import io
import json
import logging
import os
from pathlib import Path
import subprocess
import sys
import time
import uuid

import httpx
from PIL import Image, ImageDraw, ImageFont

import replica_multi_live as multi
from replica_hot_live import append
from maas_platform.replica_contract import private_json
from maas_platform.replica_multi_trial import expectations
from maas_platform.replica_startup_trial import StartupRequest, startup_policy, startup_counts, round_order
from maas_platform.replica_state import atomic_private
from maas_platform.replica_writer_v2 import ReconcileWriter


class Trial(multi.Trial):
    def __init__(self, directory, ids, protected, info):
        super().__init__(directory, ids, 0, protected)
        self.info = info
        self.native.url = self.native.url.rstrip('/')
        self.probe_key = info['key']['value']
        image = Image.new('RGB', (800, 180), 'white')
        ImageDraw.Draw(image).text((30, 55), 'HELLO STARTUP 2026',
                                   font=ImageFont.load_default(size=44), fill='black')
        buffer = io.BytesIO(); image.save(buffer, format='PNG')
        self.image = 'data:image/png;base64,' + base64.b64encode(buffer.getvalue()).decode()

    async def probe(self, ident):
        name = self.info['names'][str(ident)]
        payload = {'model': name, 'stream': False, 'messages': [{'role': 'user', 'content': [
            {'type': 'image_url', 'image_url': {'url': self.image}}]}]}
        started = time.monotonic()
        request_id = 'startup-trial-' + uuid.uuid4().hex
        response = await self.client.post('/v1/chat/completions', json=payload,
                                         headers={'X-Request-ID': request_id})
        # A failed/uncertain request is recorded, never replayed by this tool.
        append(self.root/'probes-private.jsonl', 'response', {'at_monotonic': started,
            'model': ident, 'status': response.status_code, 'request_id': request_id})
        response.raise_for_status()
        value = response.json(); content = json.loads(value['choices'][0]['message']['content'])
        text = ' '.join(row.get('text', '') for row in content['lines'])
        if 'HELLO' not in text or '2026' not in text:
            raise ValueError('OCR probe content mismatch; do not replay')
        result = {'model': ident, 'http': response.status_code, 'text_checked': True,
                  'latency_s': time.monotonic()-started, 'completed_monotonic': time.monotonic()}
        append(self.root/'probes-private.jsonl', 'success', result)
        return result

    async def prepare_round(self, name, limit):
        self.directory = self.root/name; self.directory.mkdir(mode=0o700)
        for part in ('state', 'journal'):
            (self.directory/part).mkdir(mode=0o700)
        rows = await self.rows()
        assert all(rows[i]['replicas'] == rows[i]['ready_replicas'] == 0
                   and not rows[i].get('worker_selector') for i in self.ids)
        assert not any(x['model_id'] in self.ids for x in await self.instances())
        atomic_private(self.directory/'policy.json', startup_policy(self.ids, *self.scope, limit))
        env = {**os.environ, 'MAAS_REPLICA_ENABLE_APPLY': '1',
            'MAAS_REPLICA_APPLY_CLUSTER': str(self.scope[0]),
            'MAAS_REPLICA_APPLY_MODEL_CAPS': json.dumps({str(i): 2 for i in self.ids}),
            'MAAS_REPLICA_STATE_DIR': str(self.directory/'state'),
            'MAAS_REPLICA_WRITE_JOURNAL': str(self.directory/'journal/write.json'),
            'MAAS_REPLICA_OBSERVE_POLICY_PATH': str(self.directory/'policy.json'),
            'MAAS_MULTI_CASE_DIR': str(self.directory), 'MAAS_MULTI_IDS': json.dumps(self.ids),
            'MAAS_MULTI_OWNER': str(self.scope[1])}
        os.environ.update({k: v for k, v in env.items() if k.startswith('MAAS_REPLICA_')})
        self.writer = ReconcileWriter.from_environment()
        await self.operate('adopt')
        self.log = (self.directory/'controller-private.log').open('w'); os.chmod(self.log.name, 0o600)
        self.child = subprocess.Popen([sys.executable, __file__, '--worker'], env=env,
                                      stdout=self.log, stderr=subprocess.STDOUT)
        self.event('controller_started', {'case': name, 'pid': self.child.pid})
        await self.wait(lambda: self.status().get('execution_state') == 'observe', 30)

    async def empty_trial(self):
        rows = await self.rows()
        return all(rows[i]['replicas'] == rows[i]['ready_replicas'] == 0 for i in self.ids) and not any(
            x['model_id'] in self.ids for x in await self.instances())

    async def round(self, name, limit, *, measured):
        await self.prepare_round(name, limit)
        start = time.monotonic(); self.policy(mode='apply')
        first_ready = all_ready = None; first_model_ready = {}; probes = {}; formal = []
        workers_seen = set(); peak = 0; allocated_states = {}; state_counts = {}
        next_formal = start; all_ready_ids = None
        atomic_private(self.root/'progress-private.json', {'phase': 'RUNNING', 'case': name,
                            'limit': limit, 'completed': len(self.results), 'started': start})
        pending_tasks = set()
        try:
            while time.monotonic()-start < 180:
                if self.child.poll() is not None:
                    raise RuntimeError('Isolated startup controller exited')
                rows = list((await self.rows()).values()); instances = await self.instances()
                devices = (await self.native('GET', '/v2/workers?perPage=100'))['items']
                now = time.monotonic(); elapsed = now-start; by_id = {r['id']: r for r in rows}
                counts = startup_counts(rows, instances, self.scope[0]); peak = max(peak, counts['global'])
                selected = [x for x in instances if x['model_id'] in self.ids]
                append(self.directory/'samples-private.jsonl', 'sample', {'elapsed_s': elapsed,
                    'models': rows, 'instances': instances, 'workers': devices, 'inflight': counts,
                    'status': self.status()})
                for x in selected:
                    allocated_states.setdefault(str(x['id']), {}).setdefault(x['state'], elapsed)
                    state_counts[x['state']] = state_counts.get(x['state'], 0)+1
                    if x['state'] == 'running':
                        workers_seen.add(x['worker_id'])
                for i in self.ids:
                    if by_id[i]['ready_replicas'] and any(x['model_id'] == i and x['state'] == 'running'
                                                         for x in selected):
                        first_model_ready.setdefault(i, elapsed)
                        if first_ready is None:first_ready = elapsed
                    if i in first_model_ready and i not in probes and elapsed-first_model_ready[i] >= 2:
                        probes[i] = asyncio.create_task(self.probe(i)); pending_tasks.add(probes[i])
                if now >= next_formal:
                    task = asyncio.create_task(self.probe(self.info['formal_model']))
                    formal.append(task); pending_tasks.add(task); next_formal = now+10
                for task in pending_tasks:
                    if task.done():task.result()
                if (all(by_id[i]['replicas'] == by_id[i]['ready_replicas'] == 2 for i in self.ids)
                        and len(selected) == 6 and all(x['state'] == 'running' for x in selected)
                        and self.writer.journal.read() is None):
                    if all_ready is None:
                        all_ready = elapsed; all_ready_ids = sorted(x['id'] for x in selected)
                    if len(probes) == 3 and all(t.done() for t in pending_tasks):break
                await asyncio.sleep(.5)
            else:raise TimeoutError('Startup round deadline; no replay')
            values = {i: t.result() for i, t in probes.items()}
            assert len(values) == 3 and all_ready is not None and len(self.writes()) == 6
            assert peak <= limit
            await asyncio.sleep(3)
            assert await self.vector([2, 2, 2])
            snapshot = await self.capture('final')
            assert sorted(x['id'] for x in snapshot['model-instances'] if x['model_id'] in self.ids) == all_ready_ids
            await self.operate('pause'); await self.stop()
            assert self.writer.journal.read() is None and await self.protected() == self.protected_baseline
            result = {'case': name, 'passed': True, 'measured': measured, 'startup_limit': limit,
                'first_native_ready_s': first_ready, 'all_native_ready_s': all_ready,
                'first_probe_success_s': min(x['completed_monotonic']-start for x in values.values()),
                'all_model_probes_success_s': max(x['completed_monotonic']-start for x in values.values()),
                'probe_dispatch_grace_after_ready_s': 2, 'peak_observed_global_inflight': peak,
                'controller_puts': len(self.writes()), 'ready_vector': [2, 2, 2],
                'workers_seen': sorted(workers_seen), 'probe_model_count': len(values),
                'formal_probe_latencies_s': [t.result()['latency_s'] for t in formal],
                'formal_probe_errors': 0, 'protected_models_unchanged': True,
                'first_observed_instance_states_s': allocated_states, 'sampled_state_counts': state_counts}
            atomic_private(self.directory/'result-private.json', result); self.results.append(result)
            for i in self.ids:await self.edit(i, replicas=0)
            await self.wait(self.empty_trial, 45)
            await asyncio.sleep(3)
        finally:
            for task in pending_tasks:
                if not task.done():task.cancel()
            await asyncio.gather(*pending_tasks, return_exceptions=True)

    async def run(self):
        rows = await self.rows(); self.scope = rows[self.ids[0]]['cluster_id'], rows[self.ids[0]]['owner_principal_id']
        assert self.info['key']['scope'] == ['inference'] and self.info['key']['expires_at']
        assert set(self.info['key']['allowed_model_names']) == set(self.info['names'].values())
        assert set(self.info['names']) == {str(i) for i in [*self.ids, self.info['formal_model']]}
        self.protected_baseline = await self.protected()
        assert set(self.protected_baseline) == set(self.protected_ids)
        assert self.info['formal_model'] in self.protected_ids
        workers = [w for w in (await self.native('GET', '/v2/workers?perPage=100'))['items'] if w['state'] == 'ready']
        assert len(workers) == 2
        budgets = [w['status']['gpu_devices'][0]['memory']['total']
                   - (w.get('system_reserved') or {}).get('vram', 0)
                   - w['status']['gpu_devices'][0]['memory']['allocated'] for w in workers]
        expectations(budgets, 3758096384)
        try:
            async with httpx.AsyncClient(base_url=self.native.url, trust_env=False,
                    timeout=httpx.Timeout(20, connect=5), follow_redirects=False,
                    headers={'Authorization': 'Bearer '+self.probe_key}) as client:
                self.client = client
                baseline = [await self.probe(self.info['formal_model']) for _ in range(5)]
                atomic_private(self.root/'formal-baseline-private.json', baseline)
                await self.round('prewarm', 2, measured=False)
                assert set(self.results[-1]['workers_seen']) == {w['id'] for w in workers}
                atomic_private(self.root/'cache-condition-private.json', {
                    'condition': 'prewarmed_same_approved_engine_on_both_current_workers',
                    'prewarm_workers': [w['id'] for w in workers], 'budgets': budgets,
                    'os_page_cache_controlled': False, 'cold_cache_tested': False})
                for repeat, limit in round_order():
                    await self.round(f'warm-r{repeat}-l{limit}', limit, measured=True)
                assert await self.empty_trial() and await self.protected() == self.protected_baseline
                atomic_private(self.root/'result-private.json', {'passed': True, 'rounds': self.results,
                    'measured_rounds': 9, 'protected_models_unchanged': True, 'trial_targets_zero': True})
                atomic_private(self.root/'progress-private.json', {'phase': 'COMPLETE', 'completed': 10})
        except Exception as exc:
            if self.child is not None:
                await self.operate('pause')
            atomic_private(self.root/'result-private.json', {'passed': False, 'rounds': self.results,
                'failed_case': self.directory.name if self.directory else None,
                'error': type(exc).__name__, 'message': str(exc)})
            raise
        finally:await self.stop()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--worker', action='store_true'); parser.add_argument('--output', type=Path)
    parser.add_argument('--models', nargs=3, type=int); parser.add_argument('--protected', nargs='+', type=int)
    parser.add_argument('--probe-info', type=Path); args = parser.parse_args()
    logging.basicConfig(level=logging.WARNING)
    if args.worker:
        multi.MultiReplicaRequest = StartupRequest
        from maas_platform.gpu_daily_watch import run_service
        asyncio.run(run_service(multi.worker)); return
    if (not args.output or not args.output.is_absolute() or args.output.exists()
            or not args.models or len(set(args.models)) != 3 or not args.protected
            or set(args.models) & set(args.protected) or not args.probe_info):
        parser.error('New private output, explicit model scope and inference-only probe info required')
    args.output.mkdir(mode=0o700)
    asyncio.run(Trial(args.output, args.models, args.protected, private_json(args.probe_info)).run())


if __name__ == '__main__':main()
