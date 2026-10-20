import json, logging, signal, threading, time
from .auth import AuthConnector
from .config import Settings
from .errors import ServiceError
from .service import TaskService, fingerprint

log = logging.getLogger('maas.worker')


class Worker:
    def __init__(self, service):
        self.service = service

    def run_once(self):
        self.service.recover()
        task = self.service.claim()
        if not task: return False
        stop = threading.Event()
        def renew():
            while not stop.wait(self.service.settings.heartbeat_seconds):
                try:
                    if not self.service.heartbeat(task): return
                except Exception:
                    log.warning('lease_heartbeat_unavailable task=%s', task['id'])
        thread = threading.Thread(target=renew, daemon=True); thread.start()
        try:
            identity = self.service.auth.for_task(task['credential_id'], task['project'])
            model = self.service.model(task['model'])
            self.service.auth.authorize(identity, task['model'], model)
            if fingerprint(model) != task['model_fingerprint']:
                raise ServiceError('MODEL_REVISION_CHANGED', 'Registered model configuration changed')
            started = time.monotonic()
            runtime_deadline = started + model['max_runtime_s']
            end = started + task['parameters']['delay_seconds']
            while time.monotonic() < end:
                if time.monotonic() >= runtime_deadline:
                    raise ServiceError('RUNTIME_EXCEEDED', 'Model execution runtime limit reached')
                if self.service.cancelled(task): raise ServiceError('INTERRUPTED', 'Mock execution interrupted')
                stop.wait(min(.1, max(0, end-time.monotonic())))
            if task['attempts'] <= task['parameters']['fail_first_attempts']:
                raise ServiceError('MOCK_TRANSIENT', 'Injected recoverable failure', 503, True)
            artifact = None
            if task['input_artifact_id']:
                data, media = self.service.read_task_input(task)
                artifact = {'bytes': len(data), 'media_type': media}
            output = {'outputs': {'text': task['input']['text'], 'input_artifact': artifact},
                      'model_revision': task['revision'], 'attempt': task['attempts'], 'usage': {'unit':'task','quantity':1}}
            self.service.finish(task, output=output)
        except ServiceError as exc:
            self.service.finish(task, error=exc)
        except Exception:
            # Keep lease for recovery rather than releasing an uncommitted/unknown attempt.
            log.exception('attempt_failed task=%s attempt=%s', task['id'], task['attempts'])
        finally:
            stop.set(); thread.join(timeout=10)
        return True


def main():
    logging.basicConfig(level=logging.INFO)
    settings = Settings.from_env(); service = TaskService(settings, AuthConnector(settings)); service.db.migrate()
    stop = threading.Event()
    for sig in (signal.SIGINT,signal.SIGTERM): signal.signal(sig, lambda *_: stop.set())
    last_clean = 0
    while not stop.is_set():
        try:
            worked = Worker(service).run_once()
            if time.monotonic()-last_clean > 60:
                service.cleanup(); last_clean=time.monotonic()
            if not worked: stop.wait(.5)
        except Exception:
            log.exception('worker_loop_unavailable'); stop.wait(2)

if __name__ == '__main__': main()
