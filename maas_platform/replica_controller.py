"""Watch GPUStack workers and reconcile a bounded, isolated replica policy."""

import argparse
from collections import Counter
import fcntl
from http.client import HTTPException
import json
import os
from pathlib import Path
from threading import Event, Thread
import time
from urllib.error import HTTPError
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener

from .cluster_maintenance import model_hash
from .operator_config import private_json
from .replica_policy import Service, floor_recovery_advice, plan


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class NativeAPI:
    def __init__(self, key):
        self.headers = {'Authorization': 'Bearer ' + key, 'Content-Type': 'application/json'}

    def open(self, path, payload=None, timeout=10):
        data = json.dumps(payload).encode() if payload is not None else None
        request = Request('http://127.0.0.1:18080' + path, data=data, headers=self.headers,
                          method='PUT' if data is not None else 'GET')
        return build_opener(ProxyHandler({}), NoRedirect()).open(request, timeout=timeout)

    def __call__(self, path, payload=None):
        with self.open(path, payload) as response:
            return json.load(response)

    def watch(self, cluster_id, changed, stop, log):
        failures = 0
        observed = {}
        path = f'/v2/workers?cluster_id={cluster_id}&watch=true'
        while not stop.is_set():
            connected_at = time.monotonic()
            try:
                with self.open(path, timeout=30) as response:
                    for line in response:
                        if stop.is_set():
                            return
                        if line.strip():
                            event = json.loads(line)
                            if not isinstance(event, dict):
                                raise ValueError('Invalid worker watch event')
                            data = event.get('data') or {}
                            if not isinstance(data, dict):
                                raise ValueError('Invalid worker watch payload')
                            ident = data.get('id')
                            if ident is not None:
                                state = data.get('state'), data.get('unreachable')
                                if observed.get(ident) != state:
                                    observed[ident] = state
                                    log({'phase': 'worker_event', 'worker_id': ident,
                                         'state': state[0], 'unreachable': state[1]})
                            changed.set()
                        if time.monotonic() - connected_at >= 30:
                            failures = 0
                raise OSError('Watch closed')
            except (OSError, ValueError, HTTPException) as error:
                failures += 1
                log({'phase': 'watch_interrupted', 'error': error_name(error),
                     'consecutive_failures': failures})
                changed.set()
                if failures >= 2:
                    # A usable periodic read remains available during stream failures.
                    log({'phase': 'watch_stopped', 'fallback': 'periodic_reconciliation'})
                    return
                stop.wait(2)


def error_name(error):
    return f'HTTP {error.code}' if isinstance(error, HTTPError) else type(error).__name__


def pages(api, path):
    separator = '&' if '?' in path else '?'
    rows = []
    for page in range(1, 11):
        batch = api(f'{path}{separator}perPage=100&page={page}')['items']
        rows.extend(batch)
        if len(batch) < 100:
            return rows
    raise ValueError('Pilot inventory exceeds pagination bound')


def eligible(worker):
    return (worker['state'] == 'ready' and worker.get('unreachable') is False
            and not (worker.get('maintenance') or {}).get('enabled'))


def device_budgets(workers, selector):
    devices = []
    for worker in workers:
        if not eligible(worker):
            continue
        if any((worker.get('labels') or {}).get(k) != v for k, v in selector.items()):
            continue
        gpus = (worker.get('status') or {}).get('gpu_devices') or []
        if len(gpus) != 1 or not gpus[0].get('uuid'):
            raise ValueError('Expected a single identifiable GPU per eligible worker')
        gpu = gpus[0]
        memory = gpu['memory']
        reserved = worker['system_reserved']['vram']
        values = memory['total'], memory['allocated'], reserved
        if any(type(n) is not int or n < 0 for n in values):
            raise ValueError('GPU scheduling capacity is unavailable')
        devices.append((gpu['uuid'], worker['id'], max(0, values[0] - values[1] - values[2])))
    counts = Counter(uuid for uuid, _, _ in devices)
    return {worker_id: free for uuid, worker_id, free in devices if counts[uuid] == 1}, any(
        count > 1 for count in counts.values())


class Controller:
    def __init__(self, api, config, log):
        self.api, self.log = api, log
        self.cluster = config['cluster_id']
        self.backend = config['backend']
        specs = config['services']
        self.ids = tuple(spec['id'] for spec in specs)
        self.names = tuple(spec['name'] for spec in specs)
        if (not isinstance(self.backend, str) or not self.backend or type(self.cluster) is not int or self.cluster < 1 or len(set(self.ids)) != 3
                or len(self.ids) != 3 or any(type(i) is not int or i < 1 for i in self.ids)
                or len(set(self.names)) != 3
                or any(not name.startswith('maas-replica-pilot-') for name in self.names)):
            raise ValueError('Bind three distinct pilot model IDs/names and an isolated cluster')
        self.services = tuple(Service(s['claim_bytes'], s['priority'], s['weight'], s['limit'])
                              for s in specs)
        if sum(s.limit for s in self.services) > 32:
            raise ValueError('Replica limits exceed the bounded pilot policy')

    def reconcile(self, apply=False):
        inventory = pages(self.api, '/v2/models')
        if any(m['cluster_id'] == self.cluster and m['replicas'] and m['id'] not in self.ids
               for m in inventory):
            raise ValueError('Pilot requires a cluster without other active deployments')
        models = [self.api(f'/v2/models/{i}') for i in self.ids]
        selector = models[0].get('worker_selector') or {}
        for model, ident, name, service in zip(models, self.ids, self.names, self.services):
            if (model['id'] != ident or model['name'] != name or model['cluster_id'] != self.cluster
                    or model.get('backend') != self.backend
                    or model.get('cpu_offloading') is not False
                    or model.get('distributed_inference_across_workers') is not False
                    or model.get('gpu_selector') or model.get('gpu_type_selector')
                    or (model.get('worker_selector') or {}) != selector
                    or str((model.get('env') or {}).get('GPUSTACK_MODEL_VRAM_CLAIM')) != str(service.claim)):
                raise ValueError('Pilot model configuration does not match the binding')
        current = tuple(m['replicas'] for m in models)
        workers = pages(self.api, f'/v2/workers?cluster_id={self.cluster}')
        free, conflict = device_budgets(workers, selector)
        instances = [pages(self.api, f'/v2/models/{i}/instances') for i in self.ids]
        running = {worker_id: tuple(sum(row['state'] == 'running' and row['worker_id'] == worker_id
                                        for row in rows) for rows in instances)
                   for worker_id in free}
        available = tuple(sum(counts[i] for counts in running.values()) for i in range(3))
        unsettled = any(len(rows) != n or ready != n for rows, n, ready
                        in zip(instances, current, available))
        event = {'phase': 'plan', 'configured': current, 'available': available,
                 'target': current, 'free_bytes_by_worker': free, 'duplicate_gpu': conflict}
        if not conflict and len(free) <= 2 and all(n <= s.limit for n, s in zip(current, self.services)):
            advice = floor_recovery_advice(self.services, running, free)
            if advice:
                index, worker_id, remove = advice
                starting = any(row['worker_id'] in free and row['state'] not in ('error', 'running')
                               for row in instances[index])
                if not starting:
                    event['manual_floor_recovery'] = {
                        'model_id': self.ids[index], 'release_on_worker_id': worker_id,
                        'remove_running': {ident: n for ident, n in zip(self.ids, remove) if n},
                    }
        if unsettled or conflict:
            event['reason'] = 'duplicate_gpu' if conflict else 'waiting_for_native_convergence'
            self.log(event)
            return
        target = plan(self.services, current, tuple(free.values()))
        event['target'] = target
        self.log(event)
        if not apply or target == current:
            return

        first = [i for i in range(3) if current[i] == 0 and target[i] > 0]
        if first:
            index = min(first, key=lambda i: self.services[i].priority)
        else:
            index = max(range(3), key=lambda i: target[i] - current[i])
        model = models[index]
        path = f"/v2/models/{model['id']}"
        fresh = self.api(path)
        if fresh['replicas'] != current[index] or model_hash(fresh) != model_hash(model):
            raise ValueError('Model changed while planning; write refused')
        desired = current[index] + 1
        self.log({'phase': 'write_started', 'model_id': model['id'], 'replicas': desired})
        updated = self.api(path, {**fresh, 'replicas': desired})
        if updated['id'] != model['id'] or updated['replicas'] != desired:
            raise RuntimeError('Replica write result uncertain; inspect before continuing')
        self.log({'phase': 'write_confirmed', 'model_id': model['id'], 'replicas': desired})


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', type=Path, required=True)
    parser.add_argument('--key-file', type=Path, required=True)
    parser.add_argument('--apply', action='store_true')
    parser.add_argument('--once', action='store_true')
    args = parser.parse_args()
    if (args.key_file.is_symlink() or not args.key_file.is_file()
            or args.key_file.stat().st_mode & 0o077):
        parser.error('Key file must be a private regular file')
    key = args.key_file.read_text().strip()
    if not key:
        parser.error('Key file is empty')
    api = NativeAPI(key)

    def log(event):
        print(json.dumps({'at': time.time(), **event}), flush=True)

    controller = Controller(api, private_json(args.config), log)
    if api('/version')['version'] not in ('2.2.3', 'v2.2.3'):
        raise SystemExit('Only GPUStack v2.2.3 has been reviewed')
    if args.apply:
        fd = os.open(f'/run/maas-replica-pilot-{controller.cluster}.lock',
                     os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    changed, stop = Event(), Event()
    if not args.once:
        Thread(target=api.watch, args=(controller.cluster, changed, stop, log), daemon=True).start()
    try:
        while True:
            changed.clear()
            controller.reconcile(args.apply)
            if args.once:
                return
            changed.wait(10)
            # Coalesce bursts of worker heartbeat updates.
            time.sleep(1)
    except (OSError, ValueError, RuntimeError, HTTPException) as error:
        log({'phase': 'stopped', 'error': error_name(error),
             'reason': str(error) if type(error) in (ValueError, RuntimeError) else None})
        raise SystemExit(1) from None
    finally:
        stop.set()
        if args.apply:
            os.close(fd)


if __name__ == '__main__':
    main()
