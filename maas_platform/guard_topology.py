"""Nonce-bound process identities on verified, immutable guard-only routes."""
import asyncio
import hashlib
import hmac
import ipaddress
import json
import re
import secrets
import time

import httpx

from .config import private_text


async def probe_process(client, worker, instance, gpu, route, keys):
    host = ipaddress.ip_address(worker['ip'])
    port = instance['port']
    if type(port) is not int or not 0 < port < 65536 or host != ipaddress.ip_address(instance['worker_ip']):
        raise ValueError('Invalid serving endpoint')
    secret = keys[gpu]
    if not isinstance(secret, str) or not re.fullmatch('[a-f0-9]{64}', secret):
        raise ValueError('Invalid GPU key')
    address = f'[{host}]' if host.version == 6 else str(host)
    nonce = secrets.token_hex(32)
    async with client.stream('GET', f'http://{address}:{port}/maas/guard-proof', params={'nonce': nonce}) as response:
        response.raise_for_status()
        if response.headers.get('content-encoding', 'identity') != 'identity':
            raise ValueError('Encoded guard response')
        data = bytearray()
        async for chunk in response.aiter_bytes():
            if len(data) + len(chunk) > 1024:
                raise ValueError('Oversized guard response')
            data.extend(chunk)
    proof = json.loads(data)
    process = proof['instance']
    if not re.fullmatch('[a-f0-9]{32}', process):
        raise ValueError('Invalid guard process identity')
    message = f'maas-guard-v1\n{nonce}\n{gpu}\n{route}\n{process}'.encode()
    signature = hmac.new(secret.encode(), message, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(signature, proof['signature']):
        raise ValueError('Invalid guard signature')
    return process


async def verify_processes(policy, names, native, candidates, reasons, key_file, gateway_file=None):
    workers, models, instances, routes, _ = native
    pools = {k: p for k, p in policy.pools.items() if p.guard_backend_version}
    if not pools:
        return {}
    try:
        keys = json.loads(private_text(key_file)) if key_file else {}
    except (OSError, ValueError):
        keys = {}
    try:
        evidence = json.loads(private_text(gateway_file)) if gateway_file else {}
    except (OSError, ValueError):
        evidence = {}
    proofs = {}
    semaphore = asyncio.Semaphore(32)
    # Use a separate client so the GPUStack management credential never reaches a worker.
    async with httpx.AsyncClient(trust_env=False, follow_redirects=False, timeout=2) as client:
        async def probe(ident, instance, gpu, route):
            async with semaphore:
                process = await probe_process(client, workers[instance['worker_id']], instance, gpu, route, keys)
                return ident, process

        async def verify_pool(key, pool):
            route = names[next(iter(pool.routes.values()))]
            running = {i: r for i, r in instances.items()
                       if r['model_id'] == pool.model_id and r['state'] == 'running'}
            model = models.get(pool.model_id, {})
            try:
                endpoints = [evidence['routes'][str(r)] for r in pool.routes.values()]
                live = endpoints[0]
                if (not live or any(e != live for e in endpoints) or not 0 <= time.time()-evidence['observed'] < 10
                        or any(live[i] != [pool.model_id, running[int(i)]['worker_ip'], running[int(i)]['port']]
                               for i in live)):
                    raise ValueError('Gateway scope is stale or differs from the native placements')
                reachable = {int(i): running[int(i)] for i in live}
                if (not reachable.keys() <= candidates[key].keys() or model.get('generic_proxy')
                        or any(routes[r].get('generic_proxy') for r in pool.routes.values())
                        or model.get('backend_version') != pool.guard_backend_version
                        or any(r.get('backend_version') != pool.guard_backend_version for r in reachable.values())):
                    raise ValueError('Unverified route or target')
                async with asyncio.timeout(5):
                    results = await asyncio.gather(*(probe(i, r, candidates[key][i][1], route)
                                                     for i, r in reachable.items()), return_exceptions=True)
                if any(isinstance(item, BaseException) for item in results):
                    raise ValueError('Incomplete process evidence')
                if not 0 <= time.time()-evidence['observed'] < 10:
                    raise ValueError('Gateway evidence expired during observation')
                by_id = dict(results)
                if len(set(by_id.values())) != len(by_id):
                    raise ValueError('Ambiguous process identity')
                candidates[key] = {i:candidates[key][i] for i in reachable}
                proofs[key] = by_id
                for i, process in by_id.items():
                    candidates[key][i] += (process,)
            except (KeyError, TypeError, ValueError, TimeoutError):
                candidates[key].clear()
                reasons[key] = 'guard_unverified'

        await asyncio.gather(*(verify_pool(key, pool) for key, pool in pools.items()))
    return proofs
