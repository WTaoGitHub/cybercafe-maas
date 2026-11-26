"""Summarize signed UNKNOWN-to-RESOLVED timings from a live PP trial.

Run with the path to a JSONL file with case, kind, seconds, activity and proof
fields. Each row must represent a distinct fault verified against the ledger.
Pass --verify-shared-pp for a batch that also sampled the isolated project's
slots before injection, while UNKNOWN, after settlement and after replaying the
original host report. This validates recorded observations offline.
"""
import argparse
import json
import math
from datetime import datetime
from pathlib import Path
from statistics import median


def check_shared_pp(row):
    def require(ok, message):
        if not ok:
            raise ValueError(f"{row['case']}: {message}")

    start = datetime.fromisoformat(row["unknown_since"])
    end = datetime.fromisoformat(row["resolved_at"])
    require(start.utcoffset() is not None and end.utcoffset() is not None,
            "expected timezone-aware ledger timestamps")
    require(abs((end - start).total_seconds() - row["seconds"]) < 0.000001,
            "elapsed time differs from ledger timestamps")
    require(row["unknown_observations"] >= 2 and row["proof_while_unknown"] is False,
            "missing observations before the host proof")
    require(row["slots"] == [0, 1, 0, 0], "unexpected project slot balance")
    require(row["duplicate_http"] == 409 and row["duplicate_error"] == "RUNTIME_CHALLENGE_EXPIRED"
            and row["unchanged_after_duplicate"] is True, "duplicate proof changed the ledger or was accepted")
    require(row["attempts"] == row["activities"] == 1, "expected one dispatch and one execution attempt")
    require(row["usage_events"] == row["ocr_usage_records"] == 0, "found successful usage")
    if row["kind"] == "sync":
        require(row["client_http"] == 503 and row["client_error"] == "UNKNOWN_BACKEND_ACTIVITY"
                and row["business"] == "UNKNOWN", "expected the original synchronous error")
    else:
        require(row["client_http"] == 202 and row["business"] == "FAILED", "expected a failed async task")


def summarize(rows, samples_per_kind, *, verify_shared_pp=False):
    timings = {"sync": [], "async": []}
    cases = set()
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError("Expected a JSON object for each recovery sample")
        case, kind, seconds = row["case"], row["kind"], row["seconds"]
        if (not isinstance(case, str) or not case or case in cases
                or not isinstance(kind, str) or kind not in timings or row["activity"] != "RESOLVED"
                or row["proof"] is not True or type(seconds) not in (int, float)
                or not math.isfinite(seconds) or seconds <= 0):
            raise ValueError(f"Invalid recovery sample {case!r}")
        if verify_shared_pp:
            check_shared_pp(row)
        cases.add(case)
        timings[kind].append(seconds)

    summary = {}
    for kind, values in timings.items():
        if len(values) != samples_per_kind:
            raise ValueError(f"Expected {samples_per_kind} {kind} samples, got {len(values)}")
        values.sort()
        summary[kind] = {
            "samples": len(values),
            "min_seconds": values[0],
            "median_seconds": round(median(values), 3),
            "p95_seconds": values[math.ceil(0.95 * len(values)) - 1],
            "max_seconds": values[-1],
        }
    return summary


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("samples", type=Path)
    parser.add_argument("--samples-per-kind", type=int, default=10)
    parser.add_argument("--verify-shared-pp", action="store_true")
    args = parser.parse_args()
    if args.samples_per_kind < 1:
        parser.error("--samples-per-kind must be positive")
    try:
        rows = (json.loads(line) for line in args.samples.read_text().splitlines() if line.strip())
        print(json.dumps(summarize(rows, args.samples_per_kind, verify_shared_pp=args.verify_shared_pp), indent=2))
    except (OSError, KeyError, TypeError, ValueError) as error:
        parser.error(str(error))
