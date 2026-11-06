"""Report unhealthy inputs for the replica plugin without exposing credentials."""
import argparse
from dataclasses import replace
import json
from pathlib import Path
import time

from maas_replica_execution import PendingWrite
from maas_replica_metrics import private_bytes
from maas_replica_policy import PolicyFile
from maas_replica_pressure import PressureGate, number


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--policy", required=True, type=Path)
    parser.add_argument("--pressure", required=True, type=Path)
    parser.add_argument("--journal", required=True, type=Path)
    parser.add_argument("--credential-expiry", required=True, type=Path)
    parser.add_argument("--worker-id", required=True, action="append", type=int)
    args = parser.parse_args()
    alerts = []
    now = time.time()
    try:
        policy = PolicyFile(args.policy, allow_apply=True).refresh()
        rules = policy.pressure
        # This one-shot status checks inputs. The live plugin tracks recovery.
        if rules is None:
            alerts.append("pressure_policy_missing")
        else:
            pressure = PressureGate(args.pressure).evaluate(
                replace(rules, recovery_hold_s=0), worker_ids=set(args.worker_id)
            )
            if pressure != "open":
                alerts.append(pressure)
        pending = PendingWrite(args.journal).read()
        if pending and now - args.journal.stat().st_mtime >= 60:
            alerts.append("write_unconfirmed_over_60s")
        expires = number(json.loads(private_bytes(args.credential_expiry, 128)))
        if expires - now < 7 * 86400:
            alerts.append("management_credential_expires_within_7d")
    except (OSError, ValueError, TypeError, KeyError, AttributeError) as error:
        print(json.dumps({"alerts": ["status_input_invalid"], "error_type": type(error).__name__}))
        raise SystemExit(2) from None
    print(json.dumps({"mode": policy.execution.get("mode", "observe"), "revision": policy.revision,
                      "pending": pending is not None, "alerts": alerts}))
    if alerts:
        raise SystemExit(2)


if __name__ == "__main__":
    main()
