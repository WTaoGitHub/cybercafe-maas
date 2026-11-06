"""Retirement rules for an explicitly managed E5 cluster."""
from datetime import timedelta
import re

INTERVAL = 60
SUCCESSOR_STABLE = 15 * INTERVAL
SUCCESSOR_FRESH = timedelta(minutes=2)
REPLACED_OFFLINE = timedelta(minutes=30)
LONG_OFFLINE = timedelta(hours=1)
E5_NAME = re.compile(r"e5-[0-9a-f]{24}")


def gpu_uuids(worker):
    devices = getattr(worker.status, "gpu_devices", None) or ()
    return {
        device.uuid.lower() for device in devices
        if device.uuid and re.fullmatch(
            r"GPU-[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}",
            device.uuid,
        )
    }


def maintained(worker):
    return worker.maintenance is not None and worker.maintenance.enabled


def heartbeat_age(worker, now):
    heartbeat = worker.heartbeat_time
    if heartbeat is not None and heartbeat.tzinfo is not None and heartbeat <= now:
        return now - heartbeat
    return None


def ready_cards(workers):
    cards = {}
    for worker in workers:
        if worker.state == "ready":
            for uuid in gpu_uuids(worker):
                cards.setdefault(uuid, []).append(worker)
    return cards


class RetirementPolicy:
    def __init__(self, protected_ids):
        self.protected_ids = protected_ids
        self.ready_since = {}
        self.last_scan = None

    def observe(self, workers, now, tick):
        if self.last_scan is None or tick - self.last_scan > 2 * INTERVAL:
            self.ready_since.clear()
        self.last_scan = tick
        current = {}
        for uuid, owners in ready_cards(workers).items():
            if len(owners) != 1:
                continue
            worker = owners[0]
            age = heartbeat_age(worker, now)
            if not maintained(worker) and not worker.unreachable and age is not None and age <= SUCCESSOR_FRESH:
                key = (uuid, worker.id, worker.name)
                current[key] = self.ready_since.get(key, tick)
        self.ready_since = current

    def reason(self, worker, cards, now, tick):
        if (worker.id in self.protected_ids or not E5_NAME.fullmatch(worker.name)
                or worker.state != "not_ready" or maintained(worker)
                or worker.external_id is not None):
            return None
        age = heartbeat_age(worker, now)
        if age is None:
            return None
        uuids = gpu_uuids(worker)
        if any(len(cards.get(uuid, ())) > 1 for uuid in uuids):
            return None
        uuid = next(iter(uuids)) if len(uuids) == 1 and len(worker.status.gpu_devices) == 1 else None
        if owners := cards.get(uuid):
            successor = owners[0]
            since = self.ready_since.get((uuid, successor.id, successor.name))
            freshness = heartbeat_age(successor, now)
            if (age >= REPLACED_OFFLINE and since is not None and tick - since >= SUCCESSOR_STABLE
                    and not successor.unreachable and not maintained(successor)
                    and freshness is not None and freshness <= SUCCESSOR_FRESH):
                return "same_gpu_successor"
        if age >= LONG_OFFLINE:
            return "long_offline"
        return None
