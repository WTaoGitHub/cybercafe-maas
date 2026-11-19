"""Keep the trial engine stopped until its Docker cgroup has resource caps."""
import json
import os
from pathlib import Path
import sys
import time

MEMORY_BYTES = 16 * 1024**3
CPU_COUNT = 4
CGROUP = Path('/sys/fs/cgroup')


def limits(cgroup=CGROUP):
    try:
        memory = int((cgroup / 'memory.max').read_text().strip())
        quota, period = map(int, (cgroup / 'cpu.max').read_text().split())
    except (OSError, ValueError):
        return None
    if 0 < memory <= MEMORY_BYTES and 0 < quota <= CPU_COUNT * period and period > 0:
        return {'memory_bytes': memory, 'cpu_quota': quota, 'cpu_period': period}
    return None


def wait_for_limits(timeout=120):
    deadline = time.monotonic() + timeout
    print('Waiting for trial memory/CPU cgroup caps', flush=True)
    while True:
        value = limits()
        if value:
            return value
        if time.monotonic() >= deadline:
            raise SystemExit('Trial resource caps missing; engine was not started')
        time.sleep(.25)


def main():
    if not sys.argv[1:]:
        raise SystemExit('Pass the fixed SGLang server arguments')
    # shortcut: apply caps to each new trial container; integrate runner limits before unattended recovery.
    value = wait_for_limits()
    print(json.dumps({'trial_resource_caps_verified': value}), flush=True)
    if any(name.startswith('MAAS_UOCR_EXECUTION_') for name in os.environ):
        from uocr_execution import serve

        serve(sys.argv[1:])
        return
    os.execv(sys.executable, [sys.executable, '-m', 'sglang.launch_server', *sys.argv[1:]])


if __name__ == '__main__':
    main()
