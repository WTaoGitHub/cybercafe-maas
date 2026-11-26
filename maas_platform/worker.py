import asyncio
from concurrent.futures import ThreadPoolExecutor
import logging
import os
import signal
import socket
import threading
import time
import uuid
from .auth import AuthConnector
from .adapters import adapter_for, ExecutionContext
from .capacity_admission import follows_ready_capacity
from .config import Settings
from .controller_capacity import local_slots
from .document_ocr import MAX_RENDERED_REQUEST_BYTES
from .errors import ServiceError
from .ocr_connector import inference_client
from .service import TaskService, fingerprint
from .artifact_gc import PeriodicArtifactCleanup

log = logging.getLogger('maas.worker')


async def wait_for_done(done, seconds):
    try:
        await asyncio.wait_for(done.wait(), seconds)
    except TimeoutError:
        pass


class Worker:
    def __init__(self, service, executor_id=None, stop_claiming=None):
        self.service = service
        self.executor_id = executor_id or socket.gethostname()+'-'+uuid.uuid4().hex[:12]
        self.stop_claiming = stop_claiming
        self.active = {}

    def draining(self):
        return self.stop_claiming is not None and self.stop_claiming.is_set()

    def run_once(self):
        return asyncio.run(self._run_once())

    async def _run_once(self):
        if self.draining():
            return False
        await asyncio.to_thread(self.service.recover)
        if self.draining():
            return False
        task = await asyncio.to_thread(self.service.claim)
        if not task:
            return False
        done = asyncio.Event()
        async with asyncio.TaskGroup() as background:
            background.create_task(self._renew(done))
            try:
                await self._execute(task, None)
            finally:
                done.set()
        return True

    async def _renew(self, done):
        while not done.is_set():
            await wait_for_done(done, self.service.settings.heartbeat_seconds)
            if self.active and not done.is_set():
                try:
                    await asyncio.to_thread(self.service.heartbeat_many, tuple(self.active.values()))
                except Exception:
                    log.warning('lease_heartbeat_unavailable executor=%s', self.executor_id)

    def _context(self, task):
        identity = self.service.auth.for_task(task['credential_id'], task['project'])
        model = self.service.model(task['model'])
        self.service.auth.authorize(identity, task['model'], model)
        if fingerprint(model) != task['model_fingerprint']:
            raise ServiceError('MODEL_REVISION_CHANGED', 'Registered model configuration changed')
        return ExecutionContext(task, self.service, threading.Event(),
                                time.monotonic()+model['max_runtime_s'], model, identity)

    async def _execute(self, task, client):
        if self.draining():
            await asyncio.to_thread(self.service.defer_dispatch, task, reason='EXECUTOR_DRAINING')
            log.info('task_deferred executor=%s task=%s reason=EXECUTOR_DRAINING', self.executor_id, task['id'])
            return
        started = time.monotonic()
        log.info('task_claimed executor=%s task=%s attempt=%s', self.executor_id, task['id'], task['attempts'])
        self.active[task['token']] = task
        try:
            ctx = await asyncio.to_thread(self._context, task)
            adapter = adapter_for(ctx.model['driver'])
            if adapter.requires_upstream:
                outputs = await adapter.execute_async(ctx, client, self.draining)
            else:
                outputs = await asyncio.to_thread(adapter.execute, ctx)
            committed = await asyncio.to_thread(self._finish, ctx, outputs)
            log.info('task_finished executor=%s task=%s committed=%s outcome=success', self.executor_id, task['id'], bool(committed))
        except ServiceError as exc:
            if exc.code in ('MAINTENANCE_PAUSED', 'EXECUTOR_DRAINING'):
                await asyncio.to_thread(self.service.defer_dispatch, task, reason=exc.code)
            elif exc.code != 'DISPATCH_DEFERRED':
                await asyncio.to_thread(self.service.finish, task, error=exc)
        except Exception:
            # Keep lease for recovery rather than releasing an uncommitted/unknown attempt.
            log.error('attempt_failed executor=%s task=%s attempt=%s', self.executor_id, task['id'], task['attempts'])
        finally:
            self.active.pop(task['token'])
            log.info('task_released executor=%s task=%s elapsed_seconds=%.3f', self.executor_id, task['id'], time.monotonic()-started)

    def _finish(self, ctx, outputs):
        ctx.check()
        task, model = ctx.task, ctx.model
        output = {'outputs': outputs, 'model_revision': task['revision'],
                  'attempt': task['attempts'], 'usage': {'unit': 'task', 'quantity': 1}}
        metering = None
        if model['driver'] in ('gpustack.ocr', 'gpustack.document_ocr', 'gpustack.ovisocr3'):
            from .metering import receipt
            kwargs = {}
            if model['driver'] == 'gpustack.document_ocr':
                pages = ctx.metering_pages
                if type(pages) is not int or not 1 <= pages <= 10:
                    raise ServiceError('OUTPUT_INVALID', 'Document completion has no verified page count')
                kwargs['pages'] = pages
            metering = receipt('task', task['id'], task['model'], model['revision'], ctx.metering_tokens, **kwargs)
            output['metering'] = metering
        return self.service.finish(task, output=output, metering=metering)

    async def _dispatch(self, client, slots, automatic_limit):
        pending = set()
        reported = False
        async with asyncio.TaskGroup() as requests:
            while not self.draining():
                active = len(pending)
                legacy_room = active < slots
                automatic_room = automatic_limit is not None and active < automatic_limit[0]
                if automatic_limit is not None and not automatic_room and not reported:
                    log.warning('executor_local_limit_reached executor=%s slots=%s basis=%s',
                                self.executor_id, *automatic_limit)
                    reported = True
                if not (legacy_room or automatic_room):
                    await asyncio.wait(pending, timeout=.5, return_when=asyncio.FIRST_COMPLETED)
                    continue
                try:
                    task = await asyncio.to_thread(self.service.claim, legacy_slot=legacy_room,
                                                   automatic_slot=automatic_room or automatic_limit is None)
                except Exception:
                    log.error('worker_loop_unavailable executor=%s', self.executor_id)
                    await asyncio.sleep(2)
                    continue
                if task:
                    request = requests.create_task(self._execute(task, client))
                    pending.add(request)
                    request.add_done_callback(pending.discard)
                else:
                    await asyncio.sleep(.5)

    async def _maintain(self, done):
        from .runtime_recovery import apply_verified
        cleanup = PeriodicArtifactCleanup(self.service)
        next_evidence = 0
        try:
            while not (done.is_set() or self.draining()):
                try:
                    await asyncio.to_thread(self.service.recover)
                    await asyncio.to_thread(cleanup.tick)
                    if time.monotonic() >= next_evidence:
                        next_evidence = time.monotonic() + 5
                        await asyncio.to_thread(apply_verified, self.service.db, self.service.settings)
                except Exception:
                    log.warning('worker_maintenance_unavailable executor=%s', self.executor_id)
                await wait_for_done(done, .5)
        finally:
            await asyncio.to_thread(cleanup.close)

    async def serve(self, slots):
        # Bound blocking work independently from remote requests awaiting responses.
        asyncio.get_running_loop().set_default_executor(ThreadPoolExecutor(max_workers=slots+2))
        settings = self.service.settings
        automatic_limit = None
        connection_limit = slots
        if follows_ready_capacity(settings):
            buffer = 2 * (max(settings.max_input_bytes, MAX_RENDERED_REQUEST_BYTES) + settings.max_output_bytes)
            automatic_limit = local_slots(buffer, slots)
            connection_limit = max(slots, automatic_limit[0])
            log.info('executor_automatic_capacity executor=%s slots=%s basis=%s',
                     self.executor_id, *automatic_limit)
        done = asyncio.Event()
        async with inference_client(connection_limit) as client:
            async with asyncio.TaskGroup() as background:
                background.create_task(self._renew(done))
                background.create_task(self._maintain(done))
                try:
                    await self._dispatch(client, slots, automatic_limit)
                finally:
                    done.set()


def main():
    logging.basicConfig(level=logging.INFO)
    settings = Settings.from_env()
    service = TaskService(settings, AuthConnector(settings))
    service.db.migrate()
    stop = threading.Event()
    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, lambda *_: stop.set())
    worker = Worker(service, stop_claiming=stop)
    log.info('executor_started executor=%s pid=%s legacy_slots=%s legacy_global_limit=%s legacy_project_limit=%s',
             worker.executor_id, os.getpid(), settings.executor_slots, settings.max_running, settings.max_project_running)
    try:
        asyncio.run(worker.serve(settings.executor_slots))
    finally:
        service.auth.client.close()
    log.info('executor_stopped executor=%s', worker.executor_id)


if __name__ == '__main__':
    main()
