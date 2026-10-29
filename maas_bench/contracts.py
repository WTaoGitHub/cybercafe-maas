"""Explicit parameter bounds for the E3 tuning pilot; not hardware capacity claims."""
from dataclasses import asdict, dataclass
import argparse

MAX_PARALLEL = 16
MAX_QUEUE = 1000


def integer(value, name, minimum=1, maximum=MAX_PARALLEL):
    if type(value) is not int or not minimum <= value <= maximum:
        raise ValueError(f'{name} must be an integer in {minimum}..{maximum}')
    return value


def parallel_arg(value):
    try:
        return integer(int(value), 'parallelism')
    except ValueError as exc:
        raise argparse.ArgumentTypeError(str(exc)) from None


def queue_arg(value):
    try:
        return integer(int(value), 'queue', maximum=MAX_QUEUE)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(str(exc)) from None


@dataclass(frozen=True)
class Parameters:
    replicas: int
    executors: int
    global_limit: int
    project_limit: int
    global_queue: int
    project_queue: int
    vram_claim_bytes: int = 2147483648

    def __post_init__(self):
        for name in ('replicas', 'executors', 'global_limit', 'project_limit'):
            integer(getattr(self, name), name)
        for name in ('global_queue', 'project_queue'):
            integer(getattr(self, name), name, maximum=MAX_QUEUE)
        integer(self.vram_claim_bytes, 'vram_claim_bytes', minimum=1, maximum=64 * 1024**3)
        if self.project_limit > self.global_limit or self.project_queue > self.global_queue:
            raise ValueError('Project limits must not exceed global limits')

    @property
    def async_slots(self):
        return min(self.executors, self.global_limit, self.project_limit)

    @property
    def effective_queue(self):
        return min(self.global_queue, self.project_queue)

    def to_dict(self):
        return asdict(self)
