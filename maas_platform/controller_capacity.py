"""Per-process transport limits, independent of the current GPU deployment."""
import os
from pathlib import Path
import resource


def local_slots(bytes_per_request, fallback):
    memory = []
    try:
        available = os.sysconf('SC_AVPHYS_PAGES') * os.sysconf('SC_PAGE_SIZE')
        if available > 0:
            memory.append(available)
    except (OSError, ValueError):
        pass
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
