"""Read-only producers for the replica pressure sample contract."""
import argparse
import base64
import json
import math
import os
from pathlib import Path
import ssl
import stat
import statistics
from tempfile import NamedTemporaryFile
import time
from urllib.error import HTTPError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, ProxyHandler, HTTPSHandler, Request, build_opener

from maas_replica_pressure import number


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def private_bytes(path, limit):
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, "rb") as handle:
        mode = os.fstat(handle.fileno()).st_mode
        if not stat.S_ISREG(mode) or mode & 0o077:
            raise ValueError("Expected a private regular file")
        data = handle.read(limit + 1)
    if len(data) > limit:
        raise ValueError("Input exceeds the collection limit")
    return data


def publish(path, sample):
    if path.parent.stat().st_mode & 0o077:
        raise ValueError("Sample output directory must be private")
    with NamedTemporaryFile(mode="w", dir=path.parent, delete=False) as handle:
        temp = Path(handle.name)
        try:
            json.dump(sample, handle, allow_nan=False)
            handle.flush()
            os.fsync(handle.fileno())
            temp.replace(path)
        finally:
            temp.unlink(missing_ok=True)


def counters(interface, device):
    net = Path("/sys/class/net") / interface / "statistics"
    disk = next((line.split() for line in Path("/proc/diskstats").read_text().splitlines()
                 if line.split()[:2] == device.split(":")), None)
    if disk is None:
        raise ValueError("Backing disk counters unavailable")
    return (int((net / "rx_bytes").read_text()), int((net / "tx_bytes").read_text()),
            int(disk[7]), int(disk[9]) * 512, int(disk[10]), int(disk[12]))


def counter_signals(before, after, elapsed):
    elapsed = number(elapsed)
    if not elapsed:
        raise ValueError("Counter sample window must be positive")
    rx, tx, writes, written, write_ms, busy_ms = (number(b-a) for a, b in zip(before, after, strict=True))
    return {
        "network_rx_mib_s": rx / 2**20 / elapsed,
        "network_tx_mib_s": tx / 2**20 / elapsed,
        "disk_write_mib_s": written / 2**20 / elapsed,
        "disk_write_latency_ms": write_ms / writes if writes else 0,
        "disk_busy_pct": busy_ms / (elapsed * 10),
    }


def registry_probe(url, ca, docker_config):
    endpoint = urlsplit(url)
    if (endpoint.scheme != "https" or not endpoint.hostname or endpoint.username
            or endpoint.password or endpoint.query or endpoint.fragment
            or not endpoint.path.startswith("/v2/") or "/manifests/" not in endpoint.path):
        raise ValueError("Registry probe requires an HTTPS manifest endpoint")
    auth = json.loads(private_bytes(docker_config, 65536))["auths"][endpoint.netloc]["auth"]
    # Scope the credential to the requested registry. Redirects are disabled.
    base64.b64decode(auth, validate=True)
    client = build_opener(ProxyHandler({}), NoRedirect(),
                          HTTPSHandler(context=ssl.create_default_context(cafile=ca)))
    headers = {"Authorization": "Basic " + auth,
               "Accept": "application/vnd.oci.image.manifest.v1+json, "
                         "application/vnd.docker.distribution.manifest.v2+json"}
    start = time.monotonic()
    try:
        with client.open(Request(url, headers=headers, method="HEAD"), timeout=5) as response:
            status = response.status
    except HTTPError as error:
        status = error.code
        error.close()
    except (OSError, TimeoutError):
        status = 0
    return status, {"registry_probe_error": int(not 200 <= status < 300),
                    "registry_probe_rejected": int(status == 429 or 500 <= status < 600),
                    "registry_probe_latency_ms": (time.monotonic() - start) * 1000}


def worker_sample(args):
    if not args.interface or "/" in args.interface or args.interface in (".", ".."):
        raise ValueError("Invalid network interface")
    if len(args.device.split(":")) != 2 or not all(x.isdecimal() for x in args.device.split(":")):
        raise ValueError("Expected disk major:minor")
    if not .1 <= args.interval <= 15:
        raise ValueError("Worker sampling interval out of bounds")
    observed = time.time()
    before = counters(args.interface, args.device)
    start = time.monotonic()
    time.sleep(args.interval)
    after = counters(args.interface, args.device)
    signals = counter_signals(before, after, time.monotonic() - start)
    status, registry = registry_probe(args.registry_url, args.registry_ca, args.docker_config)
    publish(args.out, {"observed_at": observed, "signals": signals | registry})
    print(json.dumps({"registry_http_status": status, "signals": signals | registry}))


def ocr_sample(args):
    endpoint = urlsplit(args.endpoint)
    if (endpoint.scheme != "http" or endpoint.hostname != "127.0.0.1" or not endpoint.port
            or endpoint.username or endpoint.password or endpoint.query or endpoint.fragment
            or endpoint.path != "/gpustack/v1/chat/completions"):
        raise ValueError("OCR probe requires the loopback MaaS application endpoint")
    if not 1 <= args.count <= 20 or not .5 <= args.interval <= 15 or args.expected_lines < 1:
        raise ValueError("OCR sampling bounds are invalid")
    image = args.image.read_bytes()
    if not image.startswith(b"\x89PNG\r\n\x1a\n") or len(image) > 10 * 2**20:
        raise ValueError("Expected a bounded PNG probe")
    url = "data:image/png;base64," + base64.b64encode(image).decode()
    body = {"model": args.model, "stream": False, "messages": [{"role": "user", "content": [
        {"type": "image_url", "image_url": {"url": url}}]}]}
    key = private_bytes(args.key_file, 8192).decode().strip()
    headers = {"Authorization": "Bearer " + key, "Content-Type": "application/json"}
    client = build_opener(ProxyHandler({}), NoRedirect())
    observed = time.time()
    elapsed, statuses = [], []
    for attempt in range(args.count):
        start = time.monotonic()
        try:
            req = Request(args.endpoint, json.dumps(body).encode(), headers, method="POST")
            with client.open(req, timeout=8) as response:
                content = json.loads(json.load(response)["choices"][0]["message"]["content"])
                if len(content["lines"]) != args.expected_lines:
                    raise ValueError("Unexpected OCR result")
                statuses.append(response.status)
                elapsed.append((time.monotonic() - start) * 1000)
        except HTTPError as error:
            statuses.append(error.code)
            error.close()
        except (OSError, ValueError, KeyError, IndexError, TypeError):
            statuses.append(0)
        if attempt + 1 < args.count:
            time.sleep(args.interval)
    signals = {"ocr_probe_error_pct": (args.count - len(elapsed)) * 100 / args.count}
    if elapsed:
        signals["ocr_probe_p95_ms"] = sorted(elapsed)[math.ceil(.95 * len(elapsed)) - 1]
    publish(args.out, {"observed_at": observed, "signals": signals})
    print(json.dumps({"statuses": statuses, "successes": len(elapsed),
                      "p50_ms": statistics.median(elapsed) if elapsed else None, **signals}))


def combine(samples, *, now, max_age):
    if not samples or not number(max_age):
        raise ValueError("At least one sample and a positive age bound are required")
    earliest = now
    merged = {}
    for sample in samples:
        stamp = number(sample["observed_at"])
        if not 0 <= now - stamp <= max_age or not sample["signals"]:
            raise ValueError("Missing, future, or stale pressure source")
        earliest = min(earliest, stamp)
        for key, raw in sample["signals"].items():
            value = number(raw)
            merged[key] = max(merged.get(key, 0), value)
    return {"observed_at": earliest, "signals": merged}


def merge_samples(args):
    samples = [json.loads(private_bytes(path, 65536)) for path in args.input]
    result = combine(samples, now=time.time(), max_age=args.max_age)
    publish(args.out, result)
    print(json.dumps(result))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    modes = parser.add_subparsers(required=True)
    worker = modes.add_parser("worker")
    worker.add_argument("--interface", required=True)
    worker.add_argument("--device", required=True)
    worker.add_argument("--interval", type=float, default=2)
    worker.add_argument("--registry-url", required=True)
    worker.add_argument("--registry-ca", type=Path, required=True)
    worker.add_argument("--docker-config", type=Path, required=True)
    worker.set_defaults(run=worker_sample)
    ocr = modes.add_parser("ocr")
    ocr.add_argument("--endpoint", required=True)
    ocr.add_argument("--key-file", type=Path, required=True)
    ocr.add_argument("--image", type=Path, required=True)
    ocr.add_argument("--model", required=True)
    ocr.add_argument("--expected-lines", type=int, required=True)
    ocr.add_argument("--count", type=int, default=10)
    ocr.add_argument("--interval", type=float, default=1)
    ocr.set_defaults(run=ocr_sample)
    merge = modes.add_parser("merge")
    merge.add_argument("--input", action="append", type=Path, required=True)
    merge.add_argument("--max-age", type=float, required=True)
    merge.set_defaults(run=merge_samples)
    for mode in (worker, ocr, merge):
        mode.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    try:
        args.run(args)
    except (OSError, ValueError, KeyError, TypeError, StopIteration) as error:
        parser.exit(1, f"Pressure sampling failed: {type(error).__name__}\n")


if __name__ == "__main__":
    main()
