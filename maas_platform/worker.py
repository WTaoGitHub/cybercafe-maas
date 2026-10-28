import json, logging, os, signal, socket, threading, time, uuid
from .auth import AuthConnector
from .adapters import adapter_for, ExecutionContext
from .config import Settings
from .errors import ServiceError
from .service import TaskService, fingerprint

log = logging.getLogger('maas.worker')


class Worker:
    def __init__(self, service, executor_id=None, stop_claiming=None):
        self.service = service
        self.executor_id = executor_id or socket.gethostname()+'-'+uuid.uuid4().hex[:12]
        self.stop_claiming = stop_claiming

    def draining(self):
        return self.stop_claiming is not None and self.stop_claiming.is_set()

    def run_once(self):
        if self.draining(): return False
        self.service.recover()
        if self.draining(): return False
        task = self.service.claim()
        if not task: return False
        if self.draining():
            self.service.defer_dispatch(task, reason='EXECUTOR_DRAINING')
            log.info('task_deferred executor=%s task=%s reason=EXECUTOR_DRAINING', self.executor_id, task['id'])
            return True
        started = time.monotonic()
        log.info('task_claimed executor=%s task=%s attempt=%s', self.executor_id, task['id'], task['attempts'])
        stop = threading.Event()
        def renew():
            while not stop.wait(self.service.settings.heartbeat_seconds):
                try:
                    if not self.service.heartbeat(task): return
                except Exception:
                    log.warning('lease_heartbeat_unavailable executor=%s task=%s', self.executor_id, task['id'])
        thread = threading.Thread(target=renew, daemon=True); thread.start()
        try:
            identity = self.service.auth.for_task(task['credential_id'], task['project'])
            model = self.service.model(task['model'])
            self.service.auth.authorize(identity, task['model'], model)
            if fingerprint(model) != task['model_fingerprint']:
                raise ServiceError('MODEL_REVISION_CHANGED', 'Registered model configuration changed')
            ctx = ExecutionContext(task, self.service, stop, time.monotonic() + model['max_runtime_s'], model, identity)
            outputs = adapter_for(model['driver']).execute(ctx)
            ctx.check()
            output = {'outputs': outputs, 'model_revision': task['revision'],
                      'attempt': task['attempts'], 'usage': {'unit':'task','quantity':1}}
            committed = self.service.finish(task, output=output)
            log.info('task_finished executor=%s task=%s committed=%s outcome=success', self.executor_id, task['id'], bool(committed))
        except ServiceError as exc:
            if exc.code == 'MAINTENANCE_PAUSED':
                self.service.defer_dispatch(task)
            else:
                self.service.finish(task, error=exc)
        except Exception:
            # Keep lease for recovery rather than releasing an uncommitted/unknown attempt.
            log.error('attempt_failed executor=%s task=%s attempt=%s', self.executor_id, task['id'], task['attempts'])
        finally:
            stop.set(); thread.join(timeout=10)
            log.info('task_released executor=%s task=%s elapsed_seconds=%.3f', self.executor_id, task['id'], time.monotonic()-started)
        return True


def main():
    logging.basicConfig(level=logging.INFO)
    settings = Settings.from_env(); service = TaskService(settings, AuthConnector(settings)); service.db.migrate()
    stop = threading.Event()
    for sig in (signal.SIGINT,signal.SIGTERM): signal.signal(sig, lambda *_: stop.set())
    worker = Worker(service, stop_claiming=stop)
    log.info('executor_started executor=%s pid=%s global_limit=%s project_limit=%s',
             worker.executor_id, os.getpid(), settings.max_running, settings.max_project_running)
    last_clean = 0
    while not stop.is_set():
        try:
            worked = worker.run_once()
            if not stop.is_set() and time.monotonic()-last_clean > 60:
                service.cleanup(); service.cleanup_orphans(); last_clean=time.monotonic()
            if not worked: stop.wait(.5)
        except Exception:
            log.error('worker_loop_unavailable executor=%s', worker.executor_id); stop.wait(2)
    log.info('executor_stopped executor=%s', worker.executor_id)

if __name__ == '__main__': main()
