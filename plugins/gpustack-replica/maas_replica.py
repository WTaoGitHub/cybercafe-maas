"""Read-only GPUStack v2.2.3 plugin lifecycle and subscription probe."""
import asyncio
import logging

from gpustack.extension import Plugin
from gpustack.schemas.workers import Worker

log = logging.getLogger(__name__)


async def observe_workers():
    while True:
        try:
            # The native subscription registers first, then replays its snapshot.
            async with asyncio.timeout(35):
                async for event in Worker.subscribe(source="maas-replica"):
                    if event.data is not None:
                        log.info("Replica probe observed worker event type=%s", event.type)
                    # Reopen periodically to verify that the snapshot is still readable.
        except asyncio.CancelledError:
            raise
        except TimeoutError:
            pass
        except Exception:
            log.exception("Replica probe subscription failed")
        await asyncio.sleep(2)


class ReplicaPlugin(Plugin):
    def async_tasks(self):
        return [observe_workers()]
