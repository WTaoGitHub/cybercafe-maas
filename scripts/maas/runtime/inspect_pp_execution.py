"""Inspect a PP task attempt against its independently recorded worker and container.

Run in the MaaS API environment with the task ID, attempt token, upstream route,
GPU UUID, guard process instance and full Docker container ID as arguments.
"""
import argparse
import hmac
import json

from maas_platform.config import Settings
from maas_platform.db import Database
from maas_platform.runtime_proof import HEX, signature, valid_identity
from maas_platform.runtime_recovery import binding_payload, host_key


def inspect_attempt(task, token, route, gpu, instance, container):
    settings = Settings.from_env()
    with Database(settings.database_url).transaction() as conn:
        conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
        row = conn.execute("""
            SELECT t.state AS business, t.attempts, b.state, b.unknown_since, b.gpu_finished_at,
                   r.identity, r.signer_boot, r.last_outcome, r.last_checked_at, r.terminal_proof,
                   EXISTS (SELECT 1 FROM task_usage_events u WHERE u.task_id=t.id) AS charged
            FROM tasks t JOIN backend_activity b ON b.task_id=t.id
            JOIN execution_runtime r ON r.instance=b.runtime_instance
            WHERE t.id=%s AND b.token=%s AND b.dispatch_route=%s AND r.route=b.dispatch_route
              AND b.execution_guard AND NOT b.guard_conflict AND b.gpu_uuid=%s AND r.gpu_uuid=b.gpu_uuid
              AND r.instance=%s AND b.gpu_instance=r.instance AND r.identity->>'container'=%s
              AND (b.dispatch_gpu_uuid=b.gpu_uuid OR EXISTS (
                  SELECT 1 FROM execution_scopes s WHERE s.id=b.guard_scope AND s.processes->>r.instance=b.gpu_uuid))
            """, (task, token, route, gpu, instance, container)).fetchone()
        if not row or not valid_identity(row["identity"]):
            raise ValueError("No matching guarded attempt and original host identity")
        proof = row["terminal_proof"]
        verified = False
        if isinstance(proof, dict):
            nonce = proof.get("nonce")
            mac = proof.get("signature")
            expected = {**binding_payload(gpu, instance, route, row["identity"]),
                        "nonce": nonce, "outcome": "terminated"}
            key = host_key(settings, gpu, row["signer_boot"])
            verified = bool(key and isinstance(nonce, str) and HEX.fullmatch(nonce)
                            and isinstance(mac, str) and HEX.fullmatch(mac)
                            and proof == {**expected, "signature": mac}
                            and hmac.compare_digest(mac, signature(key, "inspect", expected)))
        return {"state": row["state"], "business": row["business"], "attempts": row["attempts"],
                "unknown_since": row["unknown_since"], "last_checked": row["last_checked_at"],
                "host_outcome": row["last_outcome"], "terminal_proof_present": proof is not None,
                "signature_verified": verified, "gpu_finish_received": row["gpu_finished_at"] is not None,
                "charged": row["charged"], "auto_enabled": route in settings.automatic_runtime_routes()}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("task", "token", "route", "gpu", "instance", "container"):
        parser.add_argument(name)
    print(json.dumps(inspect_attempt(**vars(parser.parse_args())), default=str))
