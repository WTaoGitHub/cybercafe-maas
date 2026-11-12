"""Deployment-time, pinned native zero-score Pending deletion capability."""
import hashlib
import math
from .replica_contract import private_json

SOURCES = {
    'gpustack/policies/scorers/status_scorer.py': '2c0cb1c9f5f08065214ef4adeed56ea316447f52e4bb0d05e03b4ca3e0ca83d8',
    'gpustack/policies/scorers/offload_layer_scorer.py': '293f909d0876e49a1246b91e90ec246af260e64097e3cb00d96280b329384650',
    'gpustack/policies/scorers/placement_scorer.py': '96989734f3abcdaae1041b357c1832fc2ead08fbb5233eee1925bef53fe61e81',
    'gpustack/policies/scorers/score_chain.py': '717174c764cc408d48b4b7730b1f67cc89093b1a0f5c1b354560bed023d748f3',
    'gpustack/server/controllers.py': '35a8f9f6dbc17c7616666589a54fd0660858c44300434269271ce13f82c2db3c',
}


def approved(path, endpoint, model, rows):
    """Only unassigned, claim-free Pending scores zero in the approved code.

    SPREAD healthy scores are nonnegative plus a positive status score. This
    attestation must be refreshed whenever the native image/config is deployed;
    it is not a per-request SSH collector or a claim of native atomic CAS.
    """
    if not path or model.get('placement_strategy') != 'spread':
        return False
    try:
        proof = private_json(path)
        scores = proof['scores']
        if (proof['version'] != 'v2.2.3' or proof['source_sha256'] != SOURCES
                or proof['endpoint_sha256'] != hashlib.sha256(endpoint.encode()).hexdigest()
                or any(type(scores[k]) not in (int, float) or not math.isfinite(scores[k])
                       or scores[k] < 0 for k in ('status', 'offload', 'placement'))
                or scores['status'] <= 0):
            return False
        for row in rows:
            distributed = row.get('distributed_servers')
            if row.get('deleted_at') or (distributed and distributed.get('subordinate_workers')):
                return False
            claim = row.get('computed_resource_claim')
            if row['state'] == 'pending':
                if row.get('worker_id') is not None or row.get('gpu_indexes') or claim is not None:
                    return False
            elif row['state'] == 'running':
                if isinstance(claim, dict):
                    total, offload = claim.get('total_layers'), claim.get('offload_layers')
                    if total is not None and offload is not None and (
                            type(total) is not int or type(offload) is not int or not 0 <= offload <= total):
                        return False
            else:
                return False
        return True
    except (OSError, ValueError, KeyError, TypeError):
        return False
