"""Continuously publish pressure samples from restricted SSH commands."""
import argparse
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import subprocess
import time

from maas_replica_metrics import combine, publish


def collect(host, key, known_hosts):
    result = subprocess.run(
        ["ssh", "-o", "BatchMode=yes", "-o", "IdentitiesOnly=yes",
         "-o", "StrictHostKeyChecking=yes", "-o", f"UserKnownHostsFile={known_hosts}",
         "-o", "ConnectTimeout=3", "-i", str(key), "--", host, "sample"],
        check=True, capture_output=True, text=True, timeout=5,
    )
    sample = json.loads(result.stdout)
    if set(sample["signals"]) != {"network_rx_mib_s", "disk_write_mib_s"}:
        raise ValueError("Unexpected pressure signals")
    return sample


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sources = parser.add_mutually_exclusive_group(required=True)
    sources.add_argument("--host", action="append")
    sources.add_argument("--source", action="append", metavar="WORKER_ID=SSH_HOST")
    parser.add_argument("--key", type=Path, required=True)
    parser.add_argument("--known-hosts", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    worker_ids = []
    hosts = args.host
    if args.source:
        hosts = []
        for source in args.source:
            ident, _, host = source.partition("=")
            if not ident.isascii() or not ident.isdecimal() or int(ident) < 1 or not host:
                parser.error("Expected a positive worker ID and SSH host")
            worker_ids.append(int(ident))
            hosts.append(host)
        if len(set(worker_ids)) != len(worker_ids) or len(set(hosts)) != len(hosts):
            parser.error("Worker IDs and SSH hosts must be unique")
    last_error = None
    with ThreadPoolExecutor(max_workers=len(hosts)) as executor:
        while True:
            start = time.monotonic()
            futures = [executor.submit(collect, host, args.key, args.known_hosts)
                       for host in hosts]
            try:
                samples = [f.result() for f in futures]
                combined = combine(samples, now=time.time(), max_age=6)
                if worker_ids:
                    combined["worker_ids"] = worker_ids
                publish(args.out, combined)
                if last_error:
                    print("Pressure sources recovered", flush=True)
                last_error = None
            except (OSError, subprocess.SubprocessError, ValueError, TypeError, KeyError) as error:
                failure = type(error).__name__
                if failure != last_error:
                    print(f"Pressure sample withheld: {failure}", flush=True)
                last_error = failure
            time.sleep(max(.25, 3 - (time.monotonic() - start)))


if __name__ == "__main__":
    main()
