"""Per-process transport limits, independent of the current GPU deployment."""
import os
from pathlib import Path
import resource


def host_available_memory():
    try:
        for line in Path('/proc/meminfo').read_text().splitlines():
            key, _, value = line.partition(':')
            if key == 'MemAvailable':
                amount, unit = value.split()
                kibibytes = int(amount)
                if unit == 'kB' and kibibytes >= 0:
                    return kibibytes * 1024
                break
    except (OSError, ValueError):
        pass
    try:
        available = os.sysconf('SC_AVPHYS_PAGES') * os.sysconf('SC_PAGE_SIZE')
        return available if available >= 0 else None
    except (OSError, ValueError):
        return None


def local_slots(bytes_per_request, fallback):
    available = host_available_memory()
    memory = [] if available is None else [available]
    for limit, used in (
        ('/sys/fs/cgroup/memory.max', '/sys/fs/cgroup/memory.current'),
        ('/sys/fs/cgroup/memory/memory.limit_in_bytes', '/sys/fs/cgroup/memory/memory.usage_in_bytes'),
    ):
        try:
            memory.append(max(0, int(Path(limit).read_text()) - int(Path(used).read_text())))
        except (OSError, ValueError):
            pass

    # Allow room for the API, database, staging buffers and other host workloads.
    slots, basis = (min(memory) // (4 * bytes_per_request), 'available_memory') if memory else (
        fallback, 'unmeasured_memory')
    files, _ = resource.getrlimit(resource.RLIMIT_NOFILE)
    if files != resource.RLIM_INFINITY:
        file_slots = (files - 64) // 4
        if file_slots < slots:
            slots, basis = file_slots, 'file_descriptors'
    return max(0, slots), basis
