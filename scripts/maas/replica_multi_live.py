"""Finite simultaneous A/B/C trials; existing services are protected."""
import argparse
import asyncio
import json
import logging
import os
from pathlib import Path
import subprocess
import sys
import time
from types import SimpleNamespace

import httpx

from replica_hot_live import Trial as BaseTrial, append
from maas_platform.replica_apply import model_settings
from maas_platform.replica_contract import ControlPolicyFile, private_json
from maas_platform.replica_control import control
from maas_platform.replica_multi_trial import MultiReplicaRequest, expectations
from maas_platform.replica_state import atomic_private
from maas_platform.replica_writer_v2 import ReconcileWriter

CLAIM = 3758096384


async def worker():
    from maas_platform import gpu_daily_watch as daily
    from maas_platform.replica_observe import run
    directory = Path(os.environ['MAAS_MULTI_CASE_DIR'])
    daily.persist = lambda _db, at, session, cards: atomic_private(directory/'subscription.json',
                        {'at': at, 'session': str(session), 'gpu_count': len(cards)})
    writer = ReconcileWriter.from_environment()
    ids = json.loads(os.environ['MAAS_MULTI_IDS'])
    policy_path = Path(os.environ['MAAS_REPLICA_OBSERVE_POLICY_PATH'])

    def floor(ident):
        rule = private_json(policy_path)['models'][str(ident)]
        return max(1, rule['min_replicas'], rule.get('auto_scale_down_min_replicas', 1))

    def record(kind, value):
        if kind == 'dispatch':
            path = directory/'state/pending-control.json'
            data = private_json(path) if path.exists() else {'models': {}}
            entry = data['models'].get(str(value['model']))
            value['pending_state'] = entry
            value['pending_observed_s'] = value['monotonic']-entry['since'] if entry else None
        append(directory/'writes-private.jsonl', 'replica_'+kind, value)

    writer.request = MultiReplicaRequest(writer.request, {i: 3 for i in ids}, writer.cluster,
                            int(os.environ['MAAS_MULTI_OWNER']), record, floor)
    key = Path(os.environ['MAAS_REPLICA_MANAGEMENT_KEY_FILE']).read_text().strip()
    signals = SimpleNamespace(session=None, changed=asyncio.Event())
    loader = ControlPolicyFile(policy_path, allow_apply=True)
    async with httpx.AsyncClient(base_url=writer.request.url+'/v2/',
                                headers={'Authorization': 'Bearer '+key}, trust_env=False,
                                timeout=httpx.Timeout(30, connect=5)) as client:
        tasks = [asyncio.create_task(daily.observe(None, client, Path(os.environ['MAAS_CATALOG_FILE']), signals)),
                 asyncio.create_task(run(client, daily.listing, signals, loader,
                                         writer_factory=lambda _: writer))]
        try:
            await asyncio.gather(*tasks)
        finally:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)


class Trial(BaseTrial):
    def __init__(self, root, ids, filler, protected):
        super().__init__(root, ids[0], protected)
        self.ids, self.filler = ids, filler

    async def rows(self):
        return {r['id']: r for r in (await self.native('GET', '/v2/models?perPage=100'))['items']}

    async def instances(self):
        return (await self.native('GET', '/v2/model-instances?perPage=100'))['items']

    async def vector(self, targets, ready=None):
        rows = await self.rows()
        return ([rows[i]['replicas'] for i in self.ids] == targets
                and [rows[i]['ready_replicas'] for i in self.ids] == (targets if ready is None else ready)
                and self.writer.journal.read() is None)

    async def matching_weighted(self):
        return any([await self.vector(v) for v in self.frozen['weighted_allowed']])

    async def edit(self, ident, **changes):
        if ident not in [*self.ids, self.filler] or set(changes)-{'replicas', 'worker_selector'}:
            raise ValueError('External operation outside multi trial scope')
        row = (await self.rows())[ident]
        assert (row['cluster_id'], row['owner_principal_id']) == self.scope
        self.event('explicit_trial_edit', {'model': ident, 'fields': changes})
        updated = await self.native('PUT', f'/v2/models/{ident}', {**model_settings(row), **changes})
        assert all(updated[k] == v for k, v in changes.items())

    def policy(self, *, mode=None, weights=None, priorities=None, minima=None):
        path = self.directory/'policy.json'
        data = private_json(path)
        data['revision'] += 1
        if mode is not None:
            data['execution']['mode'] = mode
        for field, values in [('weight', weights), ('priority', priorities), ('min_replicas', minima)]:
            if values is not None:
                for ident, value in zip(self.ids, values):
                    data['models'][str(ident)][field] = value
        atomic_private(path, data)
        self.event('policy', data)

    async def operate(self, command):
        loader = ControlPolicyFile(self.directory/'policy.json', allow_apply=True)
        targets = {i: 0 for i in self.ids} if command == 'adopt' else {}
        value = await control(command, self.writer, loader.refresh(), targets, refresh=loader.refresh)
        self.event(command, value)
        return value

    async def prepare(self, name, *, single_card=False, priorities=None):
        self.directory = self.root/name
        self.directory.mkdir(mode=0o700)
        for path in ['state', 'journal']:
            (self.directory/path).mkdir(mode=0o700)
        rows = await self.rows()
        assert all(rows[i]['replicas'] == rows[i]['ready_replicas'] == 0 for i in [*self.ids, self.filler])
        assert not any(r['model_id'] in [*self.ids, self.filler] for r in await self.instances())
        selector = self.card_selectors[0] if single_card else None
        for i in self.ids:
            await self.edit(i, worker_selector=selector)
        if single_card:
            await self.edit(self.filler, worker_selector=selector, replicas=1)
            await self.wait(self.filler_ready)
        atomic_private(self.directory/'policy.json', {'schema_version': 2, 'revision': 1,
            'scope': {'cluster_id': self.scope[0], 'owner_principal_id': self.scope[1]},
            'models': {str(i): {'min_replicas': 1, 'max_replicas': 3, 'weight': 1,
                               'priority': (priorities or [1, 1, 1])[n]} for n, i in enumerate(self.ids)},
            'execution': {'mode': 'observe', 'scale_up_batch_max': 1, 'scale_down_batch_max': 1,
                          'max_inflight_global': 1, 'max_inflight_per_cluster': 1},
            'pressure': {'mode': 'disabled'}, 'reconcile': {'interval_s': 1, 'stability_s': 1}})
        env = {**os.environ, 'MAAS_REPLICA_ENABLE_APPLY': '1',
               'MAAS_REPLICA_APPLY_CLUSTER': str(self.scope[0]),
               'MAAS_REPLICA_APPLY_MODEL_CAPS': json.dumps({str(i): 3 for i in self.ids}),
               'MAAS_REPLICA_STATE_DIR': str(self.directory/'state'),
               'MAAS_REPLICA_WRITE_JOURNAL': str(self.directory/'journal/write.json'),
               'MAAS_REPLICA_OBSERVE_POLICY_PATH': str(self.directory/'policy.json'),
               'MAAS_MULTI_CASE_DIR': str(self.directory), 'MAAS_MULTI_IDS': json.dumps(self.ids),
               'MAAS_MULTI_OWNER': str(self.scope[1])}
        os.environ.update({k: v for k, v in env.items() if k.startswith('MAAS_REPLICA_')})
        self.writer = ReconcileWriter.from_environment()
        await self.operate('adopt')
        self.log = (self.directory/'controller-private.log').open('w')
        os.chmod(self.log.name, 0o600)
        self.child = subprocess.Popen([sys.executable, __file__, '--worker'], env=env,
                                      stdout=self.log, stderr=subprocess.STDOUT)
        self.event('controller_started', {'pid': self.child.pid, 'case': name})
        await self.wait(lambda: self.status().get('execution_state') == 'observe', 30)

    async def filler_ready(self):
        row = (await self.rows())[self.filler]
        return row['replicas'] == row['ready_replicas'] == 1

    async def empty(self):
        return not any(r['model_id'] in [*self.ids, self.filler] for r in await self.instances())

    async def finish(self, name, details):
        await self.operate('pause')
        await self.stop()
        assert self.writer.journal.read() is None and await self.protected() == self.protected_baseline
        rows = await self.rows()
        result = {'case': name, 'passed': True,
                  'target': [rows[i]['replicas'] for i in self.ids],
                  'ready': [rows[i]['ready_replicas'] for i in self.ids],
                  'controller_puts': len(self.writes()), 'protected_models_unchanged': True, **details}
        await self.capture('final')
        atomic_private(self.directory/'result.json', result)
        self.results.append(result)
        for i in [*self.ids, self.filler]:
            if rows[i]['replicas']:
                await self.edit(i, replicas=0)
        await self.wait(self.empty, 45)
        for i in self.ids:
            await self.edit(i, worker_selector=None)
        await self.edit(self.filler, worker_selector=self.card_selectors[0])

    async def bootstrap(self):
        self.policy(mode='apply')
        await self.wait(lambda: self.vector(self.frozen['equal']), 120)
        writes = self.writes()
        assert [w['model'] for w in writes[:3]] == self.ids
        assert all(w['before'] == 0 and w['after'] == 1 for w in writes[:3])
        assert [w['after'] for w in writes] and all(1 <= w['after'] <= 3 for w in writes)

    async def baseline_case(self):
        await self.prepare('bootstrap_equal')
        await self.bootstrap()
        await asyncio.sleep(5)
        assert await self.vector([2, 2, 2]) and len(self.writes()) == 6
        await self.finish('bootstrap_equal', {'minima_served_before_extra_replicas': True,
                                             'frozen_expected': [2, 2, 2]})

    async def weight_case(self):
        await self.prepare('weight_change')
        await self.bootstrap()
        self.policy(weights=[2, 1, 1])
        await self.wait(self.matching_weighted, 120)
        await asyncio.sleep(8)
        assert await self.matching_weighted()
        await self.finish('weight_change', {'weights': [2, 1, 1],
                'allowed': self.frozen['weighted_allowed'], 'minima_and_caps_respected': True})

    async def priority_case(self):
        await self.prepare('priority_recovery', single_card=True, priorities=[1, 2, 3])
        self.policy(mode='apply')
        await self.wait(lambda: self.vector([1, 1, 0]), 90)
        await asyncio.sleep(5)
        assert await self.vector([1, 1, 0]) and len(self.writes()) == 2
        await self.capture('limited')
        await self.edit(self.filler, replicas=0)
        await self.wait(lambda: self.vector([1, 1, 1]), 120)
        assert [w['model'] for w in self.writes()] == self.ids
        await self.finish('priority_recovery', {'priorities': [1, 2, 3],
                'limited_target': [1, 1, 0], 'capacity_release_recovered_third_model': True})

    async def pending_rows(self, minimum=False):
        rows = await self.rows()
        instances = await self.instances()
        goals = [3, 2, 2] if minimum else [3, 3, 3]
        ready = [1, 2, 2] if minimum else [2, 2, 2]
        if ([rows[i]['replicas'] for i in self.ids] != goals
                or [rows[i]['ready_replicas'] for i in self.ids] != ready):
            return False
        return all(r['state'] in ('running', 'pending') and
                   (r['state'] != 'pending' or (r.get('worker_id') is None
                    and str(r.get('state_message') or '').startswith(('No suitable workers', 'No available workers'))))
                   for r in instances if r['model_id'] in self.ids)

    async def pending_case(self, minimum=False):
        name = 'minimum_pending' if minimum else 'pending_contraction'
        await self.prepare(name)
        await self.bootstrap()
        await self.operate('pause')
        if minimum:
            await self.edit(self.ids[0], replicas=1)
            await self.wait(lambda: self.vector([1, 2, 2]), 45)
            data = await self.capture('freed-slot')
            allocations = {w['id']: sum(CLAIM for r in data['model-instances']
                           if r.get('worker_id') == w['id'] and r.get('gpu_indexes') == [0])
                           for w in data['workers'] if w['state'] == 'ready'}
            free_card = min(allocations, key=allocations.get)
            selector = next(s for ident, s in self.card_selector_map.items() if ident == free_card)
            await self.edit(self.filler, worker_selector=selector, replicas=1)
            await self.wait(self.filler_ready)
            self.policy(minima=[2, 2, 2])
            await self.edit(self.ids[0], replicas=3)
        else:
            for i in self.ids:
                await self.edit(i, replicas=3)
        await self.wait(lambda: self.pending_rows(minimum), 40)
        data = await self.capture('starved')
        protected = sorted(r['id'] for r in data['model-instances']
                           if r['model_id'] in self.ids and r['state'] == 'running')
        before = len(self.writes())
        await self.operate('resume')
        await self.wait(lambda: self.vector([2, 2, 2], [1, 2, 2] if minimum else None), 300)
        await asyncio.sleep(10)
        assert await self.vector([2, 2, 2], [1, 2, 2] if minimum else None)
        after = await self.capture('holding')
        assert sorted(r['id'] for r in after['model-instances']
                      if r['model_id'] in self.ids and r['state'] == 'running') == protected
        downs = self.writes()[before:]
        assert len(downs) == (1 if minimum else 3)
        assert all(w['after'] == w['before']-1 and w['pending_observed_s'] >= 180 for w in downs)
        assert all(w['pending_state']['phase'] == 'contracting' for w in downs)
        await self.finish(name, {'minima': [2, 2, 2] if minimum else [1, 1, 1],
                'qualified_seconds': [w['pending_observed_s'] for w in downs],
                'ready_ids_preserved': True, 'hold_observed_s': 10,
                'pending_retained_at_floor': minimum})

    async def pause_case(self):
        await self.prepare('pause_resume')
        await self.bootstrap()
        await self.operate('pause')
        for i in self.ids:
            await self.edit(i, replicas=1)
        self.policy(weights=[2, 1, 1])
        await self.wait(lambda: self.vector([1, 1, 1]), 45)
        count = len(self.writes())
        await asyncio.sleep(8)
        assert len(self.writes()) == count and self.writer.state.read()['paused']
        await self.operate('resume')
        await self.wait(self.matching_weighted, 120)
        await self.finish('pause_resume', {'paused_observed_s': 8, 'paused_additional_puts': 0,
                                          'latest_weights_applied': [2, 1, 1]})

    async def monitor(self):
        while True:
            if self.child is not None:
                append(self.directory/'samples-private.jsonl', 'status', self.status())
            await asyncio.sleep(1)

    async def run(self):
        rows = await self.rows()
        self.scope = rows[self.ids[0]]['cluster_id'], rows[self.ids[0]]['owner_principal_id']
        assert all(rows[i]['replicas'] == rows[i]['ready_replicas'] == 0
                   and rows[i]['env']['GPUSTACK_MODEL_VRAM_CLAIM'] == str(CLAIM)
                   for i in [*self.ids, self.filler])
        self.protected_baseline = await self.protected()
        assert set(self.protected_baseline) == set(self.protected_ids)
        workers = (await self.native('GET', '/v2/workers?perPage=100'))['items']
        workers = sorted([w for w in workers if w['state'] == 'ready'], key=lambda w: w['id'])
        assert len(workers) == 2
        self.card_selector_map = {w['id']: {'worker-name': w['labels']['worker-name']} for w in workers}
        self.card_selectors = list(self.card_selector_map.values())
        instances = await self.instances()
        budgets = []
        for w in workers:
            device = w['status']['gpu_devices'][0]
            native = sum(int((r.get('computed_resource_claim') or {}).get('vram', {}).get('0', 0))
                         for r in instances if r.get('worker_id') == w['id'] and r.get('gpu_indexes') == [0])
            allocated = max(device['memory']['allocated'], native)
            budgets.append(device['memory']['total']-(w.get('system_reserved') or {}).get('vram', 0)-allocated)
        self.frozen = expectations(budgets, CLAIM)
        atomic_private(self.root/'frozen-private.json', {'plan': self.frozen, 'free_bytes': budgets,
                        'model_ids': self.ids, 'claim_bytes': CLAIM})
        monitor = asyncio.create_task(self.monitor())
        try:
            await self.baseline_case()
            await self.weight_case()
            await self.priority_case()
            await self.pending_case()
            await self.pending_case(minimum=True)
            await self.pause_case()
            assert await self.empty() and await self.protected() == self.protected_baseline
            atomic_private(self.root/'result-private.json', {'passed': True, 'cases': self.results,
                        'all_trial_targets_zero': True, 'protected_models_unchanged': True})
        except Exception as exc:
            if self.child is not None:
                try:
                    await self.operate('pause')
                except (OSError, ValueError):
                    pass
            atomic_private(self.root/'result-private.json', {'passed': False, 'cases': self.results,
                'failed_case': self.directory.name if self.directory else None,
                'error': type(exc).__name__, 'message': str(exc)})
            raise
        finally:
            await self.stop()
            monitor.cancel()
            await asyncio.gather(monitor, return_exceptions=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--worker', action='store_true')
    parser.add_argument('--output', type=Path)
    parser.add_argument('--models', type=int, nargs=3)
    parser.add_argument('--filler', type=int)
    parser.add_argument('--protected', type=int, nargs='+')
    args = parser.parse_args()
    logging.basicConfig(level=logging.WARNING)
    if args.worker:
        from maas_platform.gpu_daily_watch import run_service
        asyncio.run(run_service(worker))
        return
    if (not args.output or not args.output.is_absolute() or args.output.exists() or not args.models
            or len(set(args.models)) != 3 or not args.filler or args.filler in args.models
            or not args.protected or set(args.protected) & set([*args.models, args.filler])
            or any(i < 1 for i in [*args.models, args.filler, *args.protected])):
        parser.error('New private output and distinct explicit three-model/filler/protected scope required')
    args.output.mkdir(mode=0o700)
    atomic_private(args.output/'started.json', {'at': time.time(), 'models': args.models})
    asyncio.run(Trial(args.output, args.models, args.filler, args.protected).run())


if __name__ == '__main__':
    main()
