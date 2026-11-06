"""Restricted SSH command that emits only worker receive and disk write rates."""
import argparse
import json
from pathlib import Path
import subprocess
import time


def read_counters(interface, device):
    receive = Path("/sys/class/net") / interface / "statistics" / "rx_bytes"
    disk = next(row.split() for row in Path("/proc/diskstats").read_text().splitlines()
                if row.split()[:2] == device)
    return int(receive.read_text()), int(disk[9]) * 512


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--interface", default="enp3s0")
    parser.add_argument("--docker-root", default="/var/lib/docker")
    args = parser.parse_args()
    if not args.interface or "/" in args.interface or args.interface in (".", ".."):
        parser.error("Invalid interface")
    device = subprocess.check_output(
        ["findmnt", "-no", "MAJ:MIN", "-T", args.docker_root],
        text=True, timeout=2,
    ).strip().split(":")
    stamp = time.time()
    start = time.monotonic()
    before = read_counters(args.interface, device)
    time.sleep(1)
    after = read_counters(args.interface, device)
    duration = time.monotonic() - start
    rx, write = ((b - a) / duration / 2**20 for a, b in zip(before, after, strict=True))
    if min(rx, write) < 0:
        raise ValueError("Worker counters reset")
    print(json.dumps({"observed_at": stamp, "signals": {
        "network_rx_mib_s": rx, "disk_write_mib_s": write,
    }}, allow_nan=False))


if __name__ == "__main__":
    main()
