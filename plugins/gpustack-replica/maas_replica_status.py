"""Report unhealthy inputs for the replica plugin without exposing credentials."""
import argparse
from contextlib import suppress
from dataclasses import replace
from datetime import datetime
import json
from pathlib import Path
import time
from urllib.request import ProxyHandler, Request, build_opener

from maas_replica_execution import PendingWrite
from maas_replica_metrics import NoRedirect, private_bytes
from maas_replica_policy import PolicyFile
from maas_replica_pressure import PressureGate, number


def cluster_status(port, key_path, policy, now):
    key = private_bytes(key_path, 8192).decode().strip()
    if not key or any(ch.isspace() for ch in key):
        raise ValueError("Invalid management credential")
    client = build_opener(ProxyHandler({}), NoRedirect())

    def read(path):
        request = Request(f"http://127.0.0.1:{port}{path}",
                          headers={"Authorization": "Bearer " + key})
        with client.open(request, timeout=5) as response:
            return json.load(response)

    delayed = []
    for ident in sorted(policy.models):
        path = f"/v2/models/{ident}"
        model = read(path)
        if model["id"] != ident:
            raise ValueError("Managed deployment identity mismatch")
        instances = read(path + "/instances?perPage=100")["items"]
        ready = sum(row["state"] == "running" for row in instances)
        stamp = model.get("updated_at") or model["created_at"]
        updated = datetime.fromisoformat(stamp.replace("Z", "+00:00"))
        if ready < model["replicas"] and now - updated.timestamp() >= 180:
            delayed.append(ident)
    workers = read("/v2/workers?perPage=100")
    if workers.get("pagination", {}).get("totalPage", 1) > 1:
        raise ValueError("Worker status inventory exceeds page")
    eligible = {row["id"] for row in workers["items"]
                if row["cluster_id"] == policy.cluster and row["state"] == "ready"
                and not row["unreachable"] and not (row.get("maintenance") or {}).get("enabled")
                and (row.get("status") or {}).get("gpu_devices")}
    return eligible, delayed


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--policy", required=True, type=Path)
    parser.add_argument("--pressure", required=True, type=Path)
    parser.add_argument("--journal", required=True, type=Path)
    parser.add_argument("--credential-expiry", required=True, type=Path)
    parser.add_argument("--worker-id", required=True, action="append", type=int)
    parser.add_argument("--management-port", type=int, default=18080)
    parser.add_argument("--management-key", type=Path)
    args = parser.parse_args()
    alerts = []
    now = time.time()
    delayed = []
    try:
        policy = PolicyFile(args.policy, allow_apply=True).refresh()
        rules = policy.pressure
        worker_ids = set(args.worker_id)
        if args.management_key is not None:
            try:
                eligible, delayed = cluster_status(args.management_port, args.management_key, policy, now)
                worker_ids |= eligible
                if delayed:
                    alerts.append("replicas_unready_over_180s")
            except (OSError, ValueError, TypeError, KeyError, AttributeError):
                alerts.append("management_read_failed")
        # This one-shot status checks inputs. The live plugin tracks recovery.
        if rules is None:
            alerts.append("pressure_policy_missing")
        else:
            pressure = PressureGate(args.pressure).evaluate(
                replace(rules, recovery_hold_s=0), worker_ids=worker_ids
            )
            if pressure != "open":
                alerts.append(pressure)
        pending = PendingWrite(args.journal).read()
        if pending:
            with suppress(FileNotFoundError):
                if now - args.journal.stat().st_mtime >= 60:
                    alerts.append("write_unconfirmed_over_60s")
        expires = number(json.loads(private_bytes(args.credential_expiry, 128)))
        if expires - now < 7 * 86400:
            alerts.append("management_credential_expires_within_7d")
    except (OSError, ValueError, TypeError, KeyError, AttributeError) as error:
        print(json.dumps({"alerts": ["status_input_invalid"], "error_type": type(error).__name__}))
        raise SystemExit(2) from None
    print(json.dumps({"mode": policy.execution.get("mode", "observe"), "revision": policy.revision,
                      "pending": pending is not None, "delayed_models": delayed, "alerts": alerts}))
    if alerts:
        raise SystemExit(2)


if __name__ == "__main__":
    main()
