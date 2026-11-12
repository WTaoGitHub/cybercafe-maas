"""Finite live fault trials using production reconciliation and isolated state.

Explicit invocation only. Never resets a native server, worker, VM or Docker.
The last trial intentionally retains an unresolved journal; never replay it.
"""
import argparse
import asyncio
import hashlib
import json
import logging
import os
from pathlib import Path
import signal
import socket
import subprocess
import sys
import time
from types import SimpleNamespace

import httpx
import uvicorn

from maas_replica_apply import LocalManagement
from maas_platform.replica_apply import model_settings
from maas_platform.replica_contract import ControlPolicyFile
from maas_platform.replica_control import control
from maas_platform.replica_fault_proxy import FaultGate, application, loopback
from maas_platform.replica_state import atomic_private
from maas_platform.replica_writer_v2 import ReconcileWriter


def save(path, value):
    atomic_private(path, value)


async def worker():
    # Use the same subscription state machine and v2 production loop. Only the
    # E17 persistence sink is isolated; production SQL and advisory lock stay untouched.
    from maas_platform import gpu_daily_watch as daily
    from maas_platform.replica_observe import run
    seen = Path(os.environ['MAAS_FAULT_CASE_DIR'])/'subscription.json'

    def persist(_db, at, session, cards):
        save(seen, {'at': at, 'session': str(session), 'gpu_count': len(cards)})

    daily.persist = persist
    key = Path(os.environ['MAAS_REPLICA_MANAGEMENT_KEY_FILE']).read_text().strip()
    signals = SimpleNamespace(session=None, changed=asyncio.Event())
    loader = ControlPolicyFile(Path(os.environ['MAAS_REPLICA_OBSERVE_POLICY_PATH']), allow_apply=True)
    async with httpx.AsyncClient(base_url=os.environ['MAAS_REPLICA_MANAGEMENT_URL']+'/v2/',
                                headers={'Authorization': 'Bearer '+key}, trust_env=False,
                                timeout=httpx.Timeout(30, connect=5)) as client:
        tasks = [asyncio.create_task(daily.observe(None, client,
                    Path(os.environ['MAAS_CATALOG_FILE']), signals)),
                 asyncio.create_task(run(client, daily.listing, signals, loader,
                                         writer_factory=lambda _: ReconcileWriter.from_environment()))]
        try:
            await asyncio.gather(*tasks)
        finally:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)


class Trial:
    def __init__(self, root, trial_model, protected_model):
        self.root, self.trial_model, self.protected_model = root, trial_model, protected_model
        self.real = loopback(os.environ['MAAS_REPLICA_MANAGEMENT_URL'])
        self.key_file = os.environ['MAAS_REPLICA_MANAGEMENT_KEY_FILE']
        self.native = LocalManagement(self.real, self.key_file)
        self.child = self.log = self.directory = None
        self.results = []
        self.gate = FaultGate()

    def event(self, kind, value):
        path = self.root/'events-private.jsonl'
        with path.open('a') as handle:
            os.chmod(path, 0o600)
            handle.write(json.dumps({'at': time.time(), 'kind': kind, 'value': value})+'\n')
            handle.flush()

    async def wait(self, check, *, timeout=35):
        end = time.monotonic()+timeout
        while time.monotonic() < end:
            if self.child is not None and self.child.poll() is not None:
                raise RuntimeError('Trial controller exited unexpectedly')
            value = check()
            if asyncio.iscoroutine(value):
                value = await value
            if value:
                return value
            await asyncio.sleep(.3)
        raise TimeoutError('Trial condition deadline exceeded')

    def status(self):
        path = self.directory/'state/status.json'
        if path.exists():
            return json.loads(path.read_text())
        return {}

    def policy(self, mode):
        path = self.directory/'policy.json'
        value = json.loads(path.read_text())
        value['revision'] += 1
        value['execution']['mode'] = mode
        save(path, value)
        self.event('policy', {'revision': value['revision'], 'mode': mode})

    async def operate(self, command, targets=None):
        loader = ControlPolicyFile(self.directory/'policy.json', allow_apply=True)
        value = await control(command, self.writer, loader.refresh(), targets or {}, refresh=loader.refresh)
        self.event(command, value)
        return value

    async def live(self):
        model = await self.native('GET', f'/v2/models/{self.trial_model}')
        return model['replicas'], model['ready_replicas']

    async def ready(self):
        return await self.live() == (1, 1)

    async def protected(self):
        row = await self.native('GET', f'/v2/models/{self.protected_model}')
        rows = (await self.native('GET', '/v2/model-instances?perPage=100'))['items']
        return {'model': row, 'ids': sorted(r['id'] for r in rows if r['model_id'] == self.protected_model)}

    async def reset_trial(self):
        row = await self.native('GET', f'/v2/models/{self.trial_model}')
        if row['replicas']:
            assert self.writer.journal.read() is None, 'Never reset with an unresolved write'
            self.event('explicit_trial_cleanup', {'target': 0})
            await self.native('PUT', f'/v2/models/{self.trial_model}', {**model_settings(row), 'replicas': 0})

        async def empty():
            rows = (await self.native('GET', '/v2/model-instances?perPage=100'))['items']
            return not any(r['model_id'] == self.trial_model for r in rows)

        await self.wait(empty)

    def start(self):
        assert self.child is None
        self.log = (self.directory/('controller-'+str(time.time_ns())+'-private.log')).open('w')
        os.chmod(self.log.name, 0o600)
        self.child = subprocess.Popen([sys.executable, __file__, '--worker'],
                                      stdout=self.log, stderr=subprocess.STDOUT, env=self.env)
        self.event('controller_started', {'pid': self.child.pid})

    async def stop(self):
        if self.child is None:
            return
        pid = self.child.pid
        self.child.send_signal(signal.SIGTERM)
        end = time.monotonic()+10
        while self.child.poll() is None and time.monotonic()<end:
            await asyncio.sleep(.1)
        if self.child.poll() is None:
            raise TimeoutError('Trial controller did not stop; no forced restart')
        code = self.child.returncode
        self.event('controller_stopped', {'pid': pid, 'exit': code})
        self.child = None
        self.log.close()
        assert code == 0, 'Trial controller did not exit gracefully'

    async def prepare(self, name):
        self.directory = self.root/name
        self.directory.mkdir(mode=0o700)
        for name0 in ('journal', 'state'):
            (self.directory/name0).mkdir(mode=0o700)
        self.gate.change('normal')
        self.counts_before = self.gate.counts()
        save(self.directory/'policy.json', {
            'schema_version': 2, 'revision': 1,
            'scope': {'cluster_id': self.scope[0], 'owner_principal_id': self.scope[1]},
            'models': {str(self.trial_model): {'min_replicas': 1, 'max_replicas': 1}},
            'execution': {'mode': 'observe', 'scale_up_batch_max': 1,
                          'max_inflight_global': 1, 'max_inflight_per_cluster': 1},
            'reconcile': {'interval_s': 1, 'stability_s': 1}, 'pressure': {'mode': 'disabled'}})
        self.env = {**os.environ, 'MAAS_REPLICA_MANAGEMENT_URL': self.proxy_url,
                    'MAAS_GPUSTACK_URL': self.proxy_url, 'MAAS_REPLICA_ENABLE_APPLY': '1',
                    'MAAS_REPLICA_APPLY_CLUSTER': str(self.scope[0]),
                    'MAAS_REPLICA_APPLY_MODEL_CAPS': json.dumps({str(self.trial_model): 1}),
                    'MAAS_REPLICA_WRITE_JOURNAL': str(self.directory/'journal/write.json'),
                    'MAAS_REPLICA_STATE_DIR': str(self.directory/'state'),
                    'MAAS_REPLICA_OBSERVE_POLICY_PATH': str(self.directory/'policy.json'),
                    'MAAS_FAULT_CASE_DIR': str(self.directory)}
        # Parent control operations use the exact same isolated durable paths.
        os.environ.update({key: value for key, value in self.env.items() if key.startswith('MAAS_REPLICA_')})
        self.writer = ReconcileWriter.from_environment()
        assert await self.live() == (0, 0)
        await self.operate('adopt', {self.trial_model: 0})
        self.start()
        await self.wait(lambda: self.status().get('execution_state') == 'observe'
                        and self.status().get('subscription_state') == 'connected')

    def delta(self):
        return {k: v-self.counts_before[k] for k, v in self.gate.counts().items()}

    async def finish(self, name, expected_puts, extra=None, *, unresolved=False):
        await self.operate('pause')
        await self.stop()
        counts = self.delta()
        assert counts['received_puts'] == 1 and counts['forwarded_puts'] == expected_puts
        assert counts['committed_puts'] == expected_puts
        assert bool(self.writer.journal.read()) == unresolved
        after = await self.protected()
        # updated_at/readiness are native-owned; no test may change protected settings or IDs.
        assert after['ids'] == self.baseline['ids']
        assert model_settings(after['model']) == model_settings(self.baseline['model'])
        assert after['model']['ready_replicas'] == self.baseline['model']['ready_replicas']
        value = {'case': name, 'passed': True, 'counts': counts,
                 'journal_retained': unresolved, 'protected_model_unchanged': True, **(extra or {})}
        save(self.directory/'result.json', value)
        self.results.append(value)
        if not unresolved:
            await self.reset_trial()

    async def read_fault(self, mode):
        await self.prepare(mode)
        self.gate.change(mode)
        self.policy('apply')
        await self.wait(lambda: self.status().get('snapshot_complete') is False)
        await asyncio.sleep(5)
        assert self.delta()['received_puts'] == 0 and await self.live() == (0, 0)
        failed = self.status()
        self.gate.change('normal')
        await self.wait(self.ready)
        await self.finish(mode, 1, {'blocked_snapshot_complete': failed['snapshot_complete'],
                                   'automatic_recovery': True, 'blocked_observed_s': 5})

    async def watch_fault(self):
        await self.prepare('watch_down')
        self.gate.change('watch_down')
        self.policy('apply')
        await self.wait(lambda: self.status().get('subscription_state') == 'polling_fallback')
        await self.wait(self.ready)
        assert self.status()['snapshot_complete']
        self.gate.change('normal')
        await self.wait(lambda: self.status().get('subscription_state') == 'connected')
        await self.finish('watch_down', 1, {'polling_fallback_wrote_once': True,
                                          'subscriptions_reconnected': True})

    async def write_fault(self, mode):
        await self.prepare(mode)
        self.gate.change(mode)
        self.policy('apply')
        await self.wait(lambda: self.writer.journal.read() is not None and self.gate.confirm_blocked)
        await self.wait(lambda: self.status().get('execution_state') == 'unresolved')
        await self.operate('pause')
        try:
            await self.operate('resume')
        except ValueError:
            pass
        else:
            raise AssertionError('Resume accepted an unresolved write')
        path = self.writer.journal.path
        before = hashlib.sha256(path.read_bytes()).hexdigest()
        old_pid = self.child.pid
        await self.stop()
        self.start()
        assert self.child.pid != old_pid
        await self.wait(lambda: self.status().get('execution_state') == 'unresolved')
        await asyncio.sleep(5)
        assert hashlib.sha256(path.read_bytes()).hexdigest() == before
        self.gate.change('normal')
        if mode == 'after_commit':
            await self.wait(lambda: self.writer.journal.read() is None)
            assert self.writer.state.read()['paused']
            await self.wait(self.ready)
            await self.finish(mode, 1, {'process_restarted': True, 'resume_rejected': True,
                                      'read_only_confirmation_while_paused': True})
        else:
            # Readback of the old target is not proof of non-commit. Preserve
            # the journal and pause, including after normal reads resume.
            await asyncio.sleep(6)
            assert hashlib.sha256(path.read_bytes()).hexdigest() == before
            assert await self.live() == (0, 0)
            self.policy('observe')
            await self.finish(mode, 0, {'process_restarted': True, 'resume_rejected': True,
                                      'old_target_did_not_clear_journal': True}, unresolved=True)

    async def run(self):
        row = await self.native('GET', f'/v2/models/{self.trial_model}')
        assert row['replicas'] == row['ready_replicas'] == 0
        self.scope = row['cluster_id'], row['owner_principal_id']
        self.baseline = await self.protected()
        assert self.baseline['model']['replicas'] == self.baseline['model']['ready_replicas'] > 0
        key = Path(self.key_file).read_text().strip()
        async with httpx.AsyncClient(base_url=self.real, headers={'Authorization': 'Bearer '+key},
                                     trust_env=False, timeout=httpx.Timeout(15, read=None)) as upstream:
            sock = socket.socket()
            sock.bind(('127.0.0.1', 0))
            self.proxy_url = 'http://127.0.0.1:'+str(sock.getsockname()[1])
            app = application(upstream, key, self.trial_model, self.gate)
            server = uvicorn.Server(uvicorn.Config(app, log_level='critical', access_log=False,
                                                  lifespan='off', timeout_graceful_shutdown=8))
            task = asyncio.create_task(server.serve(sockets=[sock]))
            try:
                await self.wait(lambda: server.started)
                await self.watch_fault()
                await self.read_fault('reads_down')
                await self.read_fault('incomplete_lists')
                await self.write_fault('after_commit')
                await self.write_fault('before_commit')
                save(self.root/'result-private.json', {'passed': True, 'cases': self.results,
                    'production_services_untouched': True, 'trial_final_target': (await self.live())[0],
                    'unresolved_trial_journal_preserved': True})
            except Exception as exc:
                if self.directory:
                    try:
                        await self.operate('pause')
                    except (OSError, ValueError):
                        pass
                save(self.root/'result-private.json', {'passed': False, 'cases': self.results,
                     'failed_case': self.directory.name if self.directory else None,
                     'error': type(exc).__name__, 'message': str(exc)})
                raise
            finally:
                await self.stop()
                server.should_exit = True
                await task
                sock.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--worker', action='store_true')
    parser.add_argument('--output', type=Path)
    parser.add_argument('--trial-model', type=int)
    parser.add_argument('--protected-model', type=int)
    args = parser.parse_args()
    logging.basicConfig(level=logging.WARNING)
    if args.worker:
        from maas_platform.gpu_daily_watch import run_service
        asyncio.run(run_service(worker))
        return
    if (not args.output or not args.output.is_absolute() or args.output.exists()
            or not args.trial_model or args.trial_model < 1 or not args.protected_model
            or args.protected_model < 1 or args.trial_model == args.protected_model):
        parser.error('Use a new private absolute output and distinct explicit deployment IDs')
    args.output.mkdir(mode=0o700, parents=False)
    lock = args.output/'started.json'
    save(lock, {'at': time.time(), 'trial_model': args.trial_model, 'protected_model': args.protected_model})
    asyncio.run(Trial(args.output, args.trial_model, args.protected_model).run())


if __name__ == '__main__':
    main()
