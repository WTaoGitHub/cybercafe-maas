"""Summarize signed UNKNOWN-to-RESOLVED timings from a live PP trial.

Run with the path to a JSONL file with case, kind, seconds, activity and proof
fields. Each row must represent a distinct fault verified against the ledger.
"""
import argparse
import json
import math
from pathlib import Path
from statistics import median


def summarize(rows, samples_per_kind):
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
    args = parser.parse_args()
    if args.samples_per_kind < 1:
        parser.error("--samples-per-kind must be positive")
    try:
        rows = (json.loads(line) for line in args.samples.read_text().splitlines() if line.strip())
        print(json.dumps(summarize(rows, args.samples_per_kind), indent=2))
    except (OSError, KeyError, ValueError) as error:
        parser.error(str(error))
