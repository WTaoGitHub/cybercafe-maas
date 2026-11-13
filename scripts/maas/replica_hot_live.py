"""Finite native configuration/resource trials; explicit isolated deployment only."""
import argparse
import asyncio
from copy import deepcopy
import json
import logging
import os
from pathlib import Path
import signal
import subprocess
import sys
import time
from types import SimpleNamespace

import httpx

from maas_replica_apply import LocalManagement
from maas_platform.replica_apply import model_settings
from maas_platform.replica_contract import ControlPolicyFile
from maas_platform.replica_control import control
from maas_platform.replica_hot_trial import ReplicaOnlyRequest
from maas_platform.replica_state import atomic_private
from maas_platform.replica_writer_v2 import ReconcileWriter

CLAIM = 'GPUSTACK_MODEL_VRAM_CLAIM'
SMALL, LARGE = 3758096384, 8589934592


def append(path, kind, value):
    with path.open('a') as handle:
        os.chmod(path, 0o600)
        handle.write(json.dumps({'at': time.time(), 'kind': kind, 'value': value})+'\n')
        handle.flush()


async def worker():
    from maas_platform import gpu_daily_watch as daily
    from maas_platform.replica_observe import run
    directory = Path(os.environ['MAAS_HOT_CASE_DIR'])

    def persist(_db, at, session, cards):
        atomic_private(directory/'subscription.json', {'at': at, 'session': str(session),
                                                       'gpu_count': len(cards)})

    daily.persist = persist
    writer = ReconcileWriter.from_environment()
    ident = int(os.environ['MAAS_HOT_MODEL_ID'])
    writer.request = ReplicaOnlyRequest(writer.request, ident, writer.cluster,
                        int(os.environ['MAAS_HOT_OWNER_ID']),
                        lambda kind, value: append(directory/'writes-private.jsonl', kind, value))
    key = Path(os.environ['MAAS_REPLICA_MANAGEMENT_KEY_FILE']).read_text().strip()
    signals = SimpleNamespace(session=None, changed=asyncio.Event())
    loader = ControlPolicyFile(Path(os.environ['MAAS_REPLICA_OBSERVE_POLICY_PATH']), allow_apply=True)
    async with httpx.AsyncClient(base_url=os.environ['MAAS_REPLICA_MANAGEMENT_URL']+'/v2/',
                                headers={'Authorization': 'Bearer '+key}, trust_env=False,
                                timeout=httpx.Timeout(30, connect=5)) as client:
        tasks = [asyncio.create_task(daily.observe(None, client,
                            Path(os.environ['MAAS_CATALOG_FILE']), signals)),
                 asyncio.create_task(run(client, daily.listing, signals, loader,
                                         writer_factory=lambda _: writer))]
        try:
            await asyncio.gather(*tasks)
        finally:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)


class Trial:
    def __init__(self, directory, model_id, protected_ids):
        self.root, self.ident, self.protected_ids = directory, model_id, protected_ids
        self.native = LocalManagement(os.environ['MAAS_REPLICA_MANAGEMENT_URL'],
                                      os.environ['MAAS_REPLICA_MANAGEMENT_KEY_FILE'])
        self.child = self.log = self.directory = None
        self.results = []

    def event(self, kind, value):
        append(self.root/'events-private.jsonl', kind, value)

    async def wait(self, check, timeout=90):
        end = time.monotonic()+timeout
        while time.monotonic()<end:
            if self.child is not None and self.child.poll() is not None:
                raise RuntimeError('Isolated controller exited')
            value = check()
            if asyncio.iscoroutine(value):
                value = await value
            if value:
                return value
            await asyncio.sleep(.5)
        raise TimeoutError('Native configuration trial deadline exceeded')

    async def capture(self, name):
        value = {'at': time.time()}
        for key in ('models', 'workers', 'model-instances'):
            value[key] = (await self.native('GET', '/v2/'+key+'?perPage=100'))['items']
        value['status'] = self.status()
        atomic_private(self.directory/(name+'-private.json'), value)
        return value

    def status(self):
        path = self.directory/'state/status.json' if self.directory else None
        return json.loads(path.read_text()) if path and path.exists() else {}

    async def model(self):
        return await self.native('GET', f'/v2/models/{self.ident}')

    async def instances(self):
        return [r for r in (await self.native('GET', '/v2/model-instances?perPage=100'))['items']
                if r['model_id'] == self.ident]

    async def ready(self, target):
        row = await self.model()
        instances = await self.instances()
        return (row['replicas'] == row['ready_replicas'] == target and len(instances) == target
                and all(r['state'] == 'running' for r in instances)
                and self.writer.journal.read() is None)

    def writes(self):
        path = self.directory/'writes-private.jsonl'
        rows = [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []
        return [row['value'] for row in rows if row['kind'] == 'replica_dispatch']

    def policy(self, *, mode=None, maximum=None):
        path = self.directory/'policy.json'
        data = json.loads(path.read_text())
        data['revision'] += 1
        if mode is not None:
            data['execution']['mode'] = mode
        if maximum is not None:
            data['models'][str(self.ident)]['max_replicas'] = maximum
        atomic_private(path, data)
        self.event('policy', data)

    async def operate(self, command):
        loader = ControlPolicyFile(self.directory/'policy.json', allow_apply=True)
        value = await control(command, self.writer, loader.refresh(),
                              {self.ident: 0} if command == 'adopt' else {}, refresh=loader.refresh)
        self.event(command, value)
        return value

    async def edit(self, **changes):
        if set(changes)-{'env', 'backend_version', 'description', 'replicas'}:
            raise ValueError('External test edit outside the trial configuration scope')
        row = await self.model()
        assert (row['cluster_id'], row['owner_principal_id']) == self.scope
        body = {**model_settings(row), **changes}
        self.event('explicit_trial_edit_intent', {'model': self.ident, 'fields': changes})
        result = await self.native('PUT', f'/v2/models/{self.ident}', body)
        for key, value in changes.items():
            assert result[key] == value, 'Native API did not retain requested configuration'
        return result

    async def protected(self):
        models = (await self.native('GET', '/v2/models?perPage=100'))['items']
        instances = (await self.native('GET', '/v2/model-instances?perPage=100'))['items']
        return {row['id']: {'settings': model_settings(row), 'ready': row['ready_replicas'],
                            'ids': sorted(r['id'] for r in instances if r['model_id'] == row['id'])}
                for row in models if row['id'] in self.protected_ids}

    async def stop(self):
        if self.child is None:
            return
        self.child.send_signal(signal.SIGTERM)
        end = time.monotonic()+10
        while self.child.poll() is None and time.monotonic()<end:
            await asyncio.sleep(.1)
        if self.child.poll() is None:
            raise TimeoutError('Isolated controller failed graceful stop')
        code = self.child.returncode
        self.event('controller_stopped', {'pid': self.child.pid, 'exit': code})
        self.child = None
        self.log.close()
        assert code == 0

    async def prepare(self, name, claim=SMALL):
        self.directory = self.root/name
        self.directory.mkdir(mode=0o700)
        for path in ('state', 'journal'):
            (self.directory/path).mkdir(mode=0o700)
        row = await self.model()
        assert row['replicas'] == row['ready_replicas'] == 0 and not await self.instances()
        await self.edit(env={**self.baseline['env'], CLAIM: str(claim)},
                        backend_version=self.version_a, description=self.baseline['description'])
        atomic_private(self.directory/'policy.json', {
            'schema_version': 2, 'revision': 1,
            'scope': {'cluster_id': self.scope[0], 'owner_principal_id': self.scope[1]},
            'models': {str(self.ident): {'min_replicas': 1, 'max_replicas': 1}},
            'execution': {'mode': 'observe', 'scale_up_batch_max': 1,
                          'max_inflight_global': 1, 'max_inflight_per_cluster': 1},
            'pressure': {'mode': 'disabled'}, 'reconcile': {'interval_s': 1, 'stability_s': 1}})
        env = {**os.environ, 'MAAS_REPLICA_ENABLE_APPLY': '1',
               'MAAS_REPLICA_APPLY_CLUSTER': str(self.scope[0]),
               'MAAS_REPLICA_APPLY_MODEL_CAPS': json.dumps({str(self.ident): 4}),
               'MAAS_REPLICA_STATE_DIR': str(self.directory/'state'),
               'MAAS_REPLICA_WRITE_JOURNAL': str(self.directory/'journal/write.json'),
               'MAAS_REPLICA_OBSERVE_POLICY_PATH': str(self.directory/'policy.json'),
               'MAAS_HOT_CASE_DIR': str(self.directory), 'MAAS_HOT_MODEL_ID': str(self.ident),
               'MAAS_HOT_OWNER_ID': str(self.scope[1])}
        os.environ.update({key: value for key, value in env.items() if key.startswith('MAAS_REPLICA_')})
        self.writer = ReconcileWriter.from_environment()
        await self.operate('adopt')
        self.log = (self.directory/'controller-private.log').open('w')
        os.chmod(self.log.name, 0o600)
        self.child = subprocess.Popen([sys.executable, __file__, '--worker'], env=env,
                                      stdout=self.log, stderr=subprocess.STDOUT)
        self.event('controller_started', {'pid': self.child.pid, 'case': name})
        await self.wait(lambda: self.status().get('execution_state') == 'observe'
                         and self.status().get('snapshot_complete') is True, 25)

    async def finish(self, name, extra):
        await self.operate('pause')
        await self.stop()
        assert self.writer.journal.read() is None
        assert await self.protected() == self.protected_baseline
        final = await self.model()
        value = {'case': name, 'passed': True, 'actual_target': final['replicas'],
                 'actual_ready': final['ready_replicas'], 'controller_puts': len(self.writes()),
                 'protected_models_unchanged': True, **extra}
        await self.capture('final')
        atomic_private(self.directory/'result.json', value)
        self.results.append(value)
        await self.edit(replicas=0)
        await self.wait(lambda: self.ready(0), 35)

    async def config_change(self):
        await self.prepare('config_change')
        env = {**self.baseline['env'], 'MAAS_HOT_CONFIG_STAGE': 'B'}
        await self.edit(backend_version=self.version_b, env=env,
                        description='Isolated image/command/env configuration B')
        self.policy(mode='apply')
        await self.wait(lambda: self.ready(1))
        row = await self.model()
        assert row['backend_version'] == self.version_b and row['env'] == env
        assert not self.writer.state.read()['paused']
        instances = await self.instances()
        assert all(r['backend_version'] == self.version_b for r in instances)
        async with httpx.AsyncClient(trust_env=False, timeout=15) as client:
            response = await client.get(self.native.url+f"/v2/model-instances/{instances[0]['id']}/logs",
                    params={'follow': 'false', 'tail': 300}, headers=self.native._headers())
            response.raise_for_status()
            text = response.text
        log_path = self.directory/'native-provisioning-private.log'
        log_path.write_text(text)
        os.chmod(log_path, 0o600)
        assert self.image_b in text and 'arguments: [--model ' in text
        assert 'MAAS_HOT_CONFIG_STAGE=B' in text
        submitted = self.writes()
        assert len(submitted) == 1 and submitted[0]['submitted']['backend_version'] == self.version_b
        assert submitted[0]['submitted']['env'] == env
        await self.edit(description='Ordinary edit while Apply remains enabled')
        await asyncio.sleep(5)
        assert not self.writer.state.read()['paused'] and len(self.writes()) == 1
        await self.finish('config_change', {'latest_backend_version_retained': True,
                                           'latest_environment_retained': True,
                                           'ordinary_edit_did_not_pause': True,
                                           'new_image_command_env_in_native_provisioning_log': True})

    async def mixed(self, name, old_claim, new_claim):
        await self.prepare(name, old_claim)
        self.policy(mode='apply')
        await self.wait(lambda: self.ready(1))
        before = await self.capture('old-ready')
        old = next(r for r in before['model-instances'] if r['model_id'] == self.ident)
        assert old['worker_id'] == self.worker_id and old['gpu_indexes'] == [0]
        assert int(old['computed_resource_claim']['vram']['0']) == old_claim
        card = next(g for w in before['workers'] if w['id'] == self.worker_id
                    for g in w['status']['gpu_devices'] if g['index'] == 0)
        other = sum(int((r.get('computed_resource_claim') or {}).get('vram', {}).get('0', 0))
                    for r in before['model-instances'] if r['model_id'] != self.ident
                    and r.get('worker_id') == self.worker_id and r.get('gpu_indexes') == [0])
        w = next(w for w in before['workers'] if w['id'] == self.worker_id)
        usable = card['memory']['total']-(w.get('system_reserved') or {}).get('vram', 0)-other
        expected = min(4, 1+max(0, usable-old_claim)//new_claim)
        naive = min(4, usable//new_claim)
        assert expected == 2 and naive != expected, 'Real card cannot distinguish mixed-cost accounting'
        await self.edit(env={**self.baseline['env'], CLAIM: str(new_claim)})
        self.policy(maximum=4)
        await self.wait(lambda: self.ready(expected))
        await self.wait(lambda: self.status().get('downscale_capacity_proof') == 'mixed_resource_claims')
        await asyncio.sleep(5)
        final = await self.capture('mixed-ready')
        rows = [r for r in final['model-instances'] if r['model_id'] == self.ident]
        assert next(r for r in rows if r['id'] == old['id'])['computed_resource_claim'] == old['computed_resource_claim']
        assert sorted(int(r['computed_resource_claim']['vram']['0']) for r in rows) == sorted([old_claim, new_claim])
        assert (await self.model())['env'][CLAIM] == str(new_claim)
        assert len(self.writes()) == 2 and self.writes()[-1]['submitted']['env'][CLAIM] == str(new_claim)
        assert not self.writer.state.read()['paused']
        await self.finish(name, {'old_claim_bytes': old_claim, 'new_claim_bytes': new_claim,
                'available_card_bytes_excluding_other_models': usable,
                'expected_actual_cost_target': expected, 'incorrect_repriced_target': naive,
                'old_instance_and_claim_preserved': True, 'new_instance_uses_latest_claim': True,
                'native_claims_bytes': sorted([old_claim, new_claim])})

    async def invalid(self, name, value):
        await self.prepare(name)
        env = deepcopy(self.baseline['env'])
        if value is None:
            env.pop(CLAIM)
        else:
            env[CLAIM] = value
        await self.edit(env=env)
        self.policy(mode='apply')
        reason = 'resource_claim_missing' if value is None else 'resource_claim_invalid'
        await self.wait(lambda: self.status().get('reason') == reason, 25)
        await asyncio.sleep(5)
        assert not self.writes() and not self.writer.state.read()['paused']
        assert not self.status()['snapshot_complete'] and not await self.instances()
        await self.capture('blocked')
        await self.edit(env=self.baseline['env'])
        await self.wait(lambda: self.ready(1))
        assert len(self.writes()) == 1 and not self.writer.state.read()['paused']
        await self.finish(name, {'blocked_reason': reason, 'blocked_controller_puts': 0,
                                'blocked_observed_s': 5, 'fixed_without_adoption_or_restart': True})

    async def paused_config(self):
        await self.prepare('paused_config')
        self.policy(mode='apply')
        await self.wait(lambda: self.ready(1))
        await self.operate('pause')
        await self.edit(env={**self.baseline['env'], CLAIM: str(LARGE), 'MAAS_HOT_CONFIG_STAGE': 'PAUSED'},
                        backend_version=self.version_b, replicas=0)
        await self.wait(lambda: self.ready(0), 35)
        await asyncio.sleep(5)
        assert len(self.writes()) == 1 and self.writer.state.read()['paused']
        await self.capture('paused-config')
        await self.operate('resume')
        await self.wait(lambda: self.ready(1))
        self.policy(maximum=4)
        await self.wait(lambda: self.status().get('vram_claim_bytes', {}).get(str(self.ident)) == LARGE)
        await asyncio.sleep(5)
        row = await self.model()
        assert row['replicas'] == row['ready_replicas'] == 1 and row['backend_version'] == self.version_b
        assert self.writes()[-1]['submitted']['env'][CLAIM] == str(LARGE)
        assert self.writes()[-1]['submitted']['backend_version'] == self.version_b
        assert len(self.writes()) == 2
        assert int((await self.instances())[0]['computed_resource_claim']['vram']['0']) == LARGE
        await self.finish('paused_config', {'paused_observed_s': 5, 'paused_additional_puts': 0,
                'resumed_latest_version_and_claim': True, 'policy_cap': 4,
                'actual_resource_target': 1, 'resumed_claim_bytes': LARGE})

    async def run(self):
        self.baseline = await self.model()
        self.scope = self.baseline['cluster_id'], self.baseline['owner_principal_id']
        self.version_a, self.version_b = '1.0.0', '1.0.1'
        backends = (await self.native('GET', '/v2/inference-backends?perPage=100'))['items']
        backend = next(b for b in backends if b['backend_name'] == self.baseline['backend'])
        self.image_b = backend['version_configs'][self.version_b]['image_name']
        assert self.baseline['replicas'] == self.baseline['ready_replicas'] == 0
        assert not await self.instances() and self.baseline['env'][CLAIM] == str(SMALL)
        assert set(self.baseline['worker_selector']) == {'worker-name'}
        self.protected_baseline = await self.protected()
        assert set(self.protected_baseline) == set(self.protected_ids)
        workers = (await self.native('GET', '/v2/workers?perPage=100'))['items']
        matches = [w for w in workers if w['state'] == 'ready'
                   and all((w.get('labels') or {}).get(k) == v
                           for k, v in self.baseline['worker_selector'].items())]
        assert len(matches) == 1
        self.worker_id = matches[0]['id']
        try:
            await self.config_change()
            await self.mixed('old_small_new_large', SMALL, LARGE)
            await self.mixed('old_large_new_small', LARGE, SMALL)
            await self.invalid('invalid_claim', 'not-a-number')
            await self.invalid('missing_claim', None)
            await self.paused_config()
            await self.edit(env=self.baseline['env'], backend_version=self.version_a,
                            description=self.baseline['description'])
            assert await self.protected() == self.protected_baseline
            atomic_private(self.root/'result-private.json', {'passed': True, 'cases': self.results,
                'trial_final_target': 0, 'protected_models_unchanged': True})
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


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--worker', action='store_true')
    parser.add_argument('--output', type=Path)
    parser.add_argument('--trial-model', type=int)
    parser.add_argument('--protected-model', type=int, action='append')
    args = parser.parse_args()
    logging.basicConfig(level=logging.WARNING)
    if args.worker:
        from maas_platform.gpu_daily_watch import run_service
        asyncio.run(run_service(worker))
        return
    if (not args.output or not args.output.is_absolute() or args.output.exists()
            or not args.trial_model or args.trial_model < 1 or not args.protected_model
            or args.trial_model in args.protected_model
            or any(i < 1 for i in args.protected_model)):
        parser.error('New private output and distinct explicit protected deployments required')
    args.output.mkdir(mode=0o700)
    atomic_private(args.output/'started.json', {'at': time.time(), 'trial_model': args.trial_model})
    asyncio.run(Trial(args.output, args.trial_model, args.protected_model).run())


if __name__ == '__main__':
    main()
