"""Probe isolated cold deployments prepared by an operator."""
from ..core import case, check


@case('COLD-001', 'cold_start', 'Sync upstream 503 quarantines inference on a cold deployment',
      ('negative', 'window', 'optional'), ('inference',), ('expect_cold_gateway_503',))
def sync_without_gpu_worker(c):
    check(c.config['expect_cold_gateway_503'] is True, 'Requires an isolated zero-GPU-worker deployment')
    response = c.request('POST', '/ocr', body=c.ocr_body(), inference=True)
    response.expect(503, 'UNKNOWN_BACKEND_ACTIVITY')
    check(response.json()['error']['retryable'] is False, 'Unknown execution must not advertise retry')


@case('COLD-002', 'cold_start', 'Async task is accepted but fails when a running executor receives upstream 503',
      ('negative', 'window', 'optional'), ('write', 'inference'), ('expect_cold_gateway_503',))
def async_without_gpu_worker(c):
    check(c.config['expect_cold_gateway_503'] is True, 'Requires an isolated zero-GPU-worker deployment')
    task, _ = c.submit(c.task_body(c.upload()))
    check(task['state'] == 'QUEUED', 'New task did not enter the queue')
    detail = c.wait(task['id'])
    check(detail['status'] == 'FAILED' and detail['attempts'] == 1
          and detail['error']['code'] == 'UNKNOWN_BACKEND_ACTIVITY'
          and detail['result'] is None and detail['usage'] is None,
          'Unknown execution must fail after one attempt without a result or usage')


@case('COLD-003', 'cold_start', 'Async task stays queued and cancellable with no executor',
      ('negative', 'window', 'optional'), ('write', 'inference'), ('expect_no_executors',))
def async_without_executor(c):
    check(c.config['expect_no_executors'] is True, 'Requires an isolated deployment with no executors')
    task, _ = c.submit(c.task_body(c.upload()))
    path = '/maas/v1/tasks/' + task['id']
    detail = c.request('GET', path).expect(200).json()
    check(task['state'] == 'QUEUED' and detail['status'] == 'QUEUED' and detail['attempts'] == 0,
          'Task must stay queued without an execution attempt')
    cancelled = c.request('POST', path + '/cancel').expect(200).json()
    check(cancelled['state'] == 'CANCELLED', 'Queued task must be cancellable')
    check(c.wait(task['id'])['status'] == 'CANCELLED', 'Task query did not confirm cancellation')
