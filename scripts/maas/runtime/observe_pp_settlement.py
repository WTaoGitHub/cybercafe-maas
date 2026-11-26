"""Read one PP route's ledger in the running MaaS API environment.

Run with the logical model as the sole argument. Each invocation emits a JSON
sample suitable for a timer journal. P95 includes offline time and is reported
only with at least ten signed settlements of that request kind in the last day.
"""
import hmac
import json
import math
import sys

from maas_platform.capacity_admission import Admission
from maas_platform.config import Settings
from maas_platform.db import Database
from maas_platform.runtime_proof import HEX, signature, valid_identity
from maas_platform.runtime_recovery import binding_payload, host_key


def settlement_metrics(rows, route, keys):
    durations = []
    for row in rows:
        gpu = row["gpu_uuid"]
        instance = row["instance"]
        identity = row["identity"]
        proof = row["terminal_proof"]
        key = keys.get((gpu, row.get('signer_boot'))) if 'signer_boot' in row else keys.get(gpu)
        if (not key or row["route"] != route or row["runtime_gpu"] != gpu or instance != row["gpu_instance"]
                or not valid_identity(identity) or not isinstance(proof, dict) or row["unknown_since"] is None):
            continue
        nonce = proof.get("nonce")
        if not isinstance(nonce, str) or not HEX.fullmatch(nonce):
            continue
        expected = {**binding_payload(gpu, instance, route, identity), "nonce": nonce, "outcome": "terminated"}
        mac = proof.get("signature")
        if (proof == {**expected, "signature": mac} and isinstance(mac, str) and HEX.fullmatch(mac)
                and hmac.compare_digest(mac, signature(key, "inspect", expected))
                and row["resolution_reason"] == f"Original guarded runtime terminated (host challenge {nonce})"):
            durations.append((row["updated_at"]-row["unknown_since"]).total_seconds())
    durations.sort()
    return {
        "auto_resolved_24h": len(rows),
        "proof_mismatch": len(rows) - len(durations),
        "successful_charges": sum(r["charged"] for r in rows),
        "signed_p95_seconds": round(durations[math.ceil(.95 * len(durations)) - 1], 3) if len(durations) >= 10 else None,
    }


def observe(model):
    settings = Settings.from_env()
    route = settings.catalog()[model]["upstream_model"]
    sample = {"model": model, "auto_enabled": route in settings.automatic_runtime_routes()}
    keys = {}
    with Database(settings.database_url).transaction() as c:
        c.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
        c.execute("SET LOCAL statement_timeout = '5s'")
        status = c.execute("SELECT now() AS observed, paused, capacity_policy_hash FROM maintenance WHERE id=1").fetchone()
        now = status["observed"]
        sample.update(observed_at=now.isoformat(), paused=status["paused"])
        admission = Admission(c, settings, status["capacity_policy_hash"], include_preparing=False)
        sample["snapshot_ready_slots"] = admission.pool(model).get("total") if admission.fresh(c) else None
        for kind, table in (("async", "backend_activity"), ("sync", "sync_activity")):
            counts = c.execute(f"""
                SELECT count(*) FILTER (WHERE created_at >= now()-interval '24 hours') AS dispatched_24h,
                       count(*) FILTER (WHERE unknown_since >= now()-interval '24 hours') AS new_unknown_24h,
                       count(*) FILTER (WHERE state='UNKNOWN') AS pending,
                       count(*) FILTER (WHERE state='UNKNOWN' AND unknown_since<=now()-interval '15 minutes') AS pending_15m,
                       min(unknown_since) FILTER (WHERE state='UNKNOWN') AS oldest
                FROM {table} WHERE dispatch_route=%s AND
                    (state='UNKNOWN' OR created_at >= now()-interval '24 hours'
                     OR unknown_since >= now()-interval '24 hours')""", (route,)).fetchone()
            oldest = counts.pop("oldest")
            counts["oldest_pending_seconds"] = round((now-oldest).total_seconds()) if oldest else None
            billing = ("EXISTS (SELECT 1 FROM task_usage_events u WHERE u.task_id=b.task_id)" if kind == "async"
                       else "EXISTS (SELECT 1 FROM sync_usage_events u WHERE u.token=b.token)")
            rows = c.execute(f"""
                SELECT b.gpu_instance,b.gpu_uuid,b.resolution_reason,b.updated_at,b.unknown_since,
                       r.instance,r.identity,r.signer_boot,r.terminal_proof,r.gpu_uuid AS runtime_gpu,r.route,{billing} AS charged
                FROM {table} b LEFT JOIN execution_runtime r ON r.instance=b.runtime_instance
                WHERE b.dispatch_route=%s AND b.state='RESOLVED' AND b.updated_at>=now()-interval '24 hours'
                  AND b.resolution_reason LIKE 'Original guarded runtime terminated (host challenge %%'""",
                             (route,)).fetchall()
            for row in rows:
                gpu, identity = row["gpu_uuid"], row["identity"]
                if isinstance(identity, dict) and valid_identity(identity):
                    scope = (gpu, row['signer_boot'])
                    if scope not in keys:
                        keys[scope] = host_key(settings, *scope)
            counts.update(settlement_metrics(rows, route, keys))
            sample[kind] = counts
    return sample


if __name__ == "__main__":
    print(json.dumps(observe(sys.argv[1])))
