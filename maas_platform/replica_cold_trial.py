"""Explicit single-deployment cold-trial gates; never changes production defaults."""
import json
import math
import os
from pathlib import Path
import re
import time

from maas_replica_execution import WriteNotSent
from .replica_apply import model_settings
from .replica_contract import private_json

CLAIM = 3758096384


def policy(model_id, cluster_id, owner_id):
    if any(type(x) is not int or x < 1 for x in (model_id, cluster_id, owner_id)):
        raise ValueError('Positive explicit cold-trial scope required')
    return {'schema_version': 2, 'revision': 1,
            'scope': {'cluster_id': cluster_id, 'owner_principal_id': owner_id},
            'models': {str(model_id): {'min_replicas': 1, 'max_replicas': 1}},
            'execution': {'mode': 'observe', 'scale_up_batch_max': 1,
                          'max_inflight_global': 1, 'max_inflight_per_cluster': 1},
            'pressure': {'mode': 'disabled'}, 'reconcile': {'interval_s': 1, 'stability_s': 1}}


def validate_proof(proof, spec, *, now):
    if not isinstance(proof, dict):
        raise ValueError('Authenticated single-use cold evidence required')
    at = proof.get('observed_controller_at')
    if (proof.get('test_id') != spec['test_id'] or proof.get('model_id') != spec['model_id']
            or proof.get('image_digest') != spec['image_digest']
            or proof.get('source') != 'authenticated_ssh'
            or proof.get('image_present') is not False
            or proof.get('target_model_files_present') is not False
            or type(proof.get('worker_id')) is not int or proof['worker_id'] < 1
            or proof['worker_id'] in spec['protected_worker_ids']
            or not isinstance(proof.get('gpu_uuid'), str) or not proof['gpu_uuid'].startswith('GPU-')
            or proof['gpu_uuid'] in spec['protected_gpu_uuids']
            or not isinstance(proof.get('boot_id'), str) or len(proof['boot_id']) != 36
            or type(at) not in (int, float) or not math.isfinite(at) or not 0 <= now-at <= 300
            or not isinstance(proof.get('inventory_sha256'), str)
            or not re.fullmatch('[0-9a-f]{64}', proof['inventory_sha256'])):
        raise ValueError('Cold evidence missing, stale, cached, or outside trial scope')
    return proof


class ColdRequest:
    """Observe refuses every mutation; cold mode permits exactly one validated 0->1."""
    def __init__(self, request, spec, directory, *, allow_start=False, clock=time.time):
        policy(spec['model_id'], spec['cluster_id'], spec['owner_id'])
        self.request, self.url, self.spec = request, request.url, spec
        self.directory, self.allow_start, self.clock = Path(directory), allow_start, clock

    async def __call__(self, method, path, body=None):
        if method == 'GET':
            return await self.request(method, path, body)
        s = self.spec
        if not self.allow_start or method != 'PUT' or path != f"/v2/models/{s['model_id']}":
            raise WriteNotSent('cold_trial_not_released')
        try:
            proof = validate_proof(private_json(self.directory/'cold-proof.json'), s, now=self.clock())
        except (OSError, ValueError, KeyError, TypeError):
            raise WriteNotSent('cold_trial_evidence_required') from None
        model = await self.request('GET', path)
        worker = await self.request('GET', f"/v2/workers/{proof['worker_id']}")
        devices = (worker.get('status') or {}).get('gpu_devices') or []
        selector = {'worker-name': worker['name']}
        if (model['id'] != s['model_id'] or model['cluster_id'] != s['cluster_id']
                or model['owner_principal_id'] != s['owner_id'] or model['replicas'] != 0
                or model['ready_replicas'] != 0 or model.get('worker_selector') != selector
                or (model.get('env') or {}).get('GPUSTACK_MODEL_VRAM_CLAIM') != str(CLAIM)
                or worker['state'] != 'ready' or worker['cluster_id'] != s['cluster_id']
                or (worker.get('maintenance') or {}).get('enabled', False)
                or (worker.get('labels') or {}).get('worker-name') != worker['name']
                or len(devices) != 1 or devices[0]['uuid'] != proof['gpu_uuid']
                or not isinstance(body, dict) or type(body.get('replicas')) is not int
                or body != {**model_settings(model), 'replicas': 1}):
            raise WriteNotSent('cold_trial_binding_changed')
        backends = (await self.request('GET', '/v2/inference-backends?perPage=100'))['items']
        backend = next((b for b in backends if model['backend'] in (b['id'], b['backend_name'])), None)
        image = model.get('image_name') or ((backend or {}).get('version_configs') or {}).get(
            model['backend_version'], {}).get('image_name')
        if image != s['image_digest']:
            raise WriteNotSent('cold_trial_image_changed')
        # Durable intent is deliberately never removed, even after an uncertain failure.
        try:
            fd = os.open(self.directory/'one-shot-intent.json', os.O_WRONLY|os.O_CREAT|os.O_EXCL, 0o600)
        except FileExistsError:
            raise WriteNotSent('cold_trial_single_use_consumed') from None
        with os.fdopen(fd, 'w') as handle:
            json.dump({'before': 0, 'after': 1, 'body': body, 'proof': proof}, handle)
            handle.flush(); os.fsync(handle.fileno())
        directory_fd = os.open(self.directory, os.O_RDONLY)
        try:os.fsync(directory_fd)
        finally:os.close(directory_fd)
        return await self.request(method, path, body)
