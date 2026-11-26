from datetime import datetime, timedelta, timezone

from maas_platform.runtime_proof import signature
from maas_platform.runtime_recovery import binding_payload
from scripts.maas.runtime.observe_pp_settlement import settlement_metrics


def test_only_bound_signed_rows_count_towards_p95():
    key, nonce, instance = "b" * 64, "c" * 64, "d" * 32
    route, gpu = "test_agent_pp", "GPU-11111111-1111-4111-8111-111111111111"
    identity = {"boot": "11111111-1111-4111-8111-111111111111", "container": "a" * 64,
                "cgroup": "/docker/" + "a" * 64, "inode": 1, "pid": 2, "started": "2026-11-25T00:00:00Z"}
    payload = {**binding_payload(gpu, instance, route, identity), "outcome": "terminated", "nonce": nonce}
    proof = {**payload, "signature": signature(key, "inspect", payload)}
    start = datetime(2026, 11, 26, tzinfo=timezone.utc)
    row = dict(terminal_proof=proof, instance=instance, gpu_instance=instance, identity=identity,
               route=route, gpu_uuid=gpu, runtime_gpu=gpu, unknown_since=start, charged=False,
               resolution_reason=f"Original guarded runtime terminated (host challenge {nonce})")
    rows = [dict(row, updated_at=start + timedelta(seconds=i)) for i in range(1, 21)]
    keys = {gpu: key}
    invalid = [dict(rows[0], route="another-route"), dict(rows[0], terminal_proof=None),
               dict(rows[0], charged=True, terminal_proof={**proof, "signature": "0" * 64})]
    assert settlement_metrics(rows[:9] + invalid, route, keys)["signed_p95_seconds"] is None
    assert settlement_metrics(rows + invalid, route, keys) == {
        "auto_resolved_24h": 23, "proof_mismatch": 3, "successful_charges": 1, "signed_p95_seconds": 19,
    }
