"""Bounded v2 pilot on an explicitly prepared deployment; never creates/deletes it."""
import argparse
import asyncio
from dataclasses import asdict
import fcntl
import json
import os
from pathlib import Path
import time

from maas_replica_apply import LocalManagement
from maas_replica_execution import PendingWrite
from maas_replica_pressure import PressureGate
from .replica_apply import deployment_binding
from .replica_contract import ControlPolicyFile
from .replica_observe import native_devices
from .replica_reconcile import ReconcileObserve
from .replica_state import ControlState, atomic_private
from .replica_writer_v2 import ReconcileWriter


class PilotRequest:
    """The pilot transport may only update its own model by exactly +1."""
    def __init__(self, request, model_id):
        self.request, self.model_id = request, model_id
        self.last_target = None
        self.puts = 0

    async def __call__(self, method, path, body=None):
        if method != 'GET':
            if (method != 'PUT' or path != f'/v2/models/{self.model_id}'
                    or self.last_target is None or body['replicas'] != self.last_target + 1):
                raise ValueError('Pilot transport refused an unauthorized mutation')
            self.puts += 1
        value = await self.request(method, path, body)
        if method == 'GET' and path == f'/v2/models/{self.model_id}':
            self.last_target = value['replicas']
        return value


def pilot_policy(model, *, maximum, limit, pressure_mode, parallel, stability=2):
    if (type(maximum) is not int or maximum not in range(1, 5)
            or type(limit) is not int or limit not in (1, 2, 4)
            or pressure_mode not in ('disabled', 'advisory', 'required')
            or type(parallel) is not bool):
        raise ValueError('Invalid bounded pilot settings')
    pressure = {'mode': pressure_mode}
    if pressure_mode != 'disabled':
        pressure.update(max_sample_age_s=20, recovery_hold_s=2, require_worker_coverage=True,
                        signals={'network_rx_mib_s': {'low': 40, 'high': 150}})
    return {
        'schema_version': 2, 'revision': 1,
        'scope': {'cluster_id': model['cluster_id'], 'owner_principal_id': model['owner_principal_id']},
        'models': {str(model['id']): {'max_replicas': maximum,
                                    'vram_claim_bytes': int(model['env']['GPUSTACK_MODEL_VRAM_CLAIM'])}},
        'execution': {'mode': 'apply', 'scale_up_batch_max': 1,
                      'max_inflight_global': limit, 'max_inflight_per_cluster': limit},
        'pressure': pressure, 'reconcile': {'interval_s': 1, 'stability_s': stability},
        'startup': {'parallel_startup': parallel},
    }


async def inventory(request, kind):
    result = {}
    for page in range(1, 11):
        batch = await request('GET', f'/v2/{kind}?perPage=100&page={page}')
        for row in batch['items']:
            if row['id'] in result:
                raise ValueError('Duplicate pilot inventory')
            result[row['id']] = row
        paging = batch.get('pagination')
        if paging is not None:
            if paging['page'] != page:
                raise ValueError('Unexpected pilot inventory page')
            if page >= paging['totalPage']:
                if len(result) != paging['total']:
                    raise ValueError('Incomplete pilot inventory')
                return result
        elif len(batch['items']) < 100:
            return result
    raise ValueError('Pilot inventory bound exceeded')


async def exercise(request, directory, *, model_id, protected_ids, maximum=1, limit=1,
                   pressure_mode='disabled', parallel=False, deadline_s=180,
                   expected_block=None, sample_case='missing'):
    directory = Path(directory)
    if (not directory.is_absolute() or type(model_id) is not int or model_id < 1
            or not protected_ids or model_id in protected_ids
            or any(type(i) is not int or i < 1 for i in protected_ids)
            or not 5 <= deadline_s <= 600
            or expected_block not in (None, 'pressure_unavailable', 'pressure_high')
            or sample_case not in ('missing', 'stale', 'high', 'recover')):
        raise ValueError('Explicit isolated pilot scope and bounded deadline required')
    if ((expected_block == 'pressure_unavailable' and
         (pressure_mode != 'required' or sample_case not in ('missing', 'stale')))
            or (expected_block == 'pressure_high' and
                (pressure_mode == 'disabled' or sample_case != 'high'))):
        raise ValueError('Expected-block pilot settings must prohibit all writes')
    # A fresh directory prevents accidentally replaying a stopped pilot.
    directory.mkdir(mode=0o700, exist_ok=False)
    lock = (directory/'pilot.lock').open('w')
    os.chmod(lock.name, 0o600)
    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    transport = PilotRequest(request, model_id)
    started = time.monotonic()
    summary = {'phase': 'PREPARING', 'model_id': model_id, 'maximum': maximum,
               'startup_limit': limit, 'parallel': parallel, 'pressure_mode': pressure_mode,
               'pressure_case': sample_case, 'synthetic_pressure_input': sample_case != 'missing',
               'native_write_attempts': 0, 'source': 'bounded_pilot_polling',
               'cache_state': 'unknown', 'first_native_ready_s': None, 'all_native_ready_s': None}
    journal = PendingWrite(directory/'write.json')

    def checkpoint():
        summary['elapsed_s'] = time.monotonic() - started
        summary['native_write_attempts'] = transport.puts
        atomic_private(directory/'status.json', summary)

    try:
        if (await transport('GET', '/version')).get('version') != 'v2.2.3':
            raise ValueError('Pilot requires pinned native version')
        rows = await inventory(transport, 'models')
        model = rows[model_id]
        if model['replicas'] != 0 or model['ready_replicas'] != 0:
            raise ValueError('Pilot must start from explicitly prepared zero target')
        if any(r['model_id'] == model_id for r in (await inventory(transport, 'model-instances')).values()):
            raise ValueError('Pilot still has native instance records')
        protected = {key: (rows[key]['replicas'], deployment_binding(rows[key])) for key in protected_ids}
        atomic_private(directory/'protected.json', {str(k): list(v) for k, v in protected.items()})
        atomic_private(directory/'policy.json', pilot_policy(
            model, maximum=maximum, limit=limit, pressure_mode=pressure_mode, parallel=parallel))
        loader = ControlPolicyFile(directory/'policy.json', allow_apply=True)
        policy = loader.refresh()
        state = ControlState(directory/'state')
        state.adopt(policy, {model_id: model}, expected_targets={model_id: 0})
        pressure = PressureGate(directory/'pressure.json')
        writer = ReconcileWriter(model['cluster_id'], {model_id: maximum}, journal, state,
                                 transport, pressure=pressure)
        observer = ReconcileObserve(native_devices, pressure, state_directory=directory/'state')
        observed = {}

        async def listing(client, kind):
            result = await inventory(transport, kind)
            if kind == 'models':
                for key, expected in protected.items():
                    if (result[key]['replicas'], deployment_binding(result[key])) != expected:
                        raise ValueError('Protected native deployment changed; pilot stopped')
            observed[kind] = result
            return result

        summary['phase'] = 'RUNNING'
        checkpoint()
        while time.monotonic() - started < deadline_s:
            if sample_case != 'missing':
                workers = await inventory(transport, 'workers')
                ids = sorted({w['id'] for w in workers.values()
                              if w['cluster_id'] == policy.cluster and w['state'] == 'ready'})
                stamp = time.time() - (60 if sample_case == 'stale' else 0)
                high = sample_case == 'high' or (sample_case == 'recover' and time.monotonic()-started < 6)
                atomic_private(directory/'pressure.json', {
                    'observed_at': stamp, 'worker_ids': ids,
                    'signals': {'network_rx_mib_s': 200 if high else 0}})
            plan = await observer.tick(None, listing, policy, subscribed=False, pending_journal=journal)
            if not plan.get('snapshot_complete'):
                raise ValueError('Pilot snapshot could not be verified')
            outcome = await writer.reconcile(policy, observer, refresh=loader.refresh)
            models = observed['models']
            instances = [r for r in observed['model-instances'].values() if r['model_id'] == model_id]
            now = time.monotonic() - started
            trace = {'elapsed_s': now, 'execution_state': outcome, 'write_attempted': writer.write_attempted,
                     'target': models[model_id]['replicas'], 'ready': models[model_id]['ready_replicas'],
                     'parallel_safe': plan['parallel_safe'], 'startup_reservation': plan['startup_reservation'],
                     'global_inflight': plan['global_inflight'], 'cluster_inflight': plan['cluster_inflight'],
                     'instances': [{k: row.get(k) for k in ('id', 'worker_id', 'state', 'gpu_indexes')}
                                   for row in instances]}
            with (directory/'trace.jsonl').open('a') as handle:
                os.chmod(handle.name, 0o600)
                handle.write(json.dumps(trace)+'\n')
            summary['latest'] = trace
            if any(r['state'] in ('error', 'unreachable') for r in instances):
                raise ValueError('Native pilot instance failed')
            if outcome in ('unresolved', 'conflict', 'confirmation_unresolved', 'converging'):
                raise ValueError('Pilot write requires inspection; no replay')
            if expected_block and outcome == expected_block and now >= 5:
                if transport.puts or models[model_id]['replicas'] != 0:
                    raise ValueError('Expected blocked pilot unexpectedly wrote')
                summary['phase'] = 'EXPECTED_BLOCK_CONFIRMED'
                checkpoint()
                return summary
            if any(r['state'] == 'running' for r in instances) and summary['first_native_ready_s'] is None:
                summary['first_native_ready_s'] = now
            if (models[model_id]['replicas'] == maximum and len(instances) == maximum
                    and all(r['state'] == 'running' for r in instances) and journal.read() is None):
                summary.update(phase='NATIVE_READY', all_native_ready_s=now,
                               confirmed_target=state.read()['models'][str(model_id)]['target'])
                checkpoint()
                return summary
            checkpoint()
            await asyncio.sleep(1)
        raise TimeoutError('Bounded pilot deadline reached; no automatic reset')
    except BaseException as error:
        summary.update(phase='STOPPED_REQUIRES_INSPECTION', error_class=type(error).__name__,
                       pending_write=asdict(journal.read()) if journal.read() else None)
        checkpoint()
        raise
    finally:
        lock.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--model-id', type=int, required=True)
    parser.add_argument('--protected-model-id', type=int, action='append', required=True)
    parser.add_argument('--directory', type=Path, required=True)
    parser.add_argument('--max-replicas', type=int, choices=range(1, 5), default=1)
    parser.add_argument('--startup-limit', type=int, choices=(1, 2, 4), default=1)
    parser.add_argument('--parallel', action='store_true')
    parser.add_argument('--pressure-mode', choices=('required', 'advisory', 'disabled'), default='disabled')
    parser.add_argument('--pressure-case', choices=('missing', 'stale', 'high', 'recover'), default='missing')
    parser.add_argument('--expected-block', choices=('pressure_unavailable', 'pressure_high'))
    parser.add_argument('--deadline', type=int, default=180)
    parser.add_argument('--permit-pilot-writes', action='store_true', required=True)
    args = parser.parse_args()
    try:
        request = LocalManagement(os.environ['MAAS_REPLICA_MANAGEMENT_URL'],
                                  os.environ['MAAS_REPLICA_MANAGEMENT_KEY_FILE'])
        value = asyncio.run(exercise(request, args.directory, model_id=args.model_id,
                                    protected_ids=args.protected_model_id, maximum=args.max_replicas,
                                    limit=args.startup_limit, parallel=args.parallel,
                                    pressure_mode=args.pressure_mode, deadline_s=args.deadline,
                                    expected_block=args.expected_block, sample_case=args.pressure_case))
        print(json.dumps({'phase': value['phase'], 'native_write_attempts': value['native_write_attempts'],
                          'first_native_ready_s': value['first_native_ready_s'],
                          'all_native_ready_s': value['all_native_ready_s']}))
    except (OSError, ValueError, KeyError, TypeError, TimeoutError):
        parser.exit(1, 'Pilot stopped; inspect its private checkpoint before another action\n')


if __name__ == '__main__':
    main()
