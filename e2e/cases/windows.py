"""Observe externally prepared conditions. Never create maintenance or faults."""
from ..core import case, check


@case('MAINT-001', 'maintenance', 'Synchronous inference blocked in externally prepared maintenance',
      ('maintenance', 'window', 'optional'), ('inference',), ('expect_maintenance',))
def paused(c):
    check(c.config['expect_maintenance'] is True, 'Requires an operator-confirmed maintenance window')
    c.request('POST', '/ocr', body=c.ocr_body(), inference=True).expect(503, 'MAINTENANCE_PAUSED')


@case('MAINT-002', 'maintenance', 'Queued task cancellation while dispatch is externally paused',
      ('maintenance', 'window', 'cancellation', 'optional'), ('write', 'inference'), ('expect_maintenance',))
def queued_cancel(c):
    check(c.config['expect_maintenance'] is True, 'Requires an operator-confirmed maintenance window')
    task, _ = c.submit(c.task_body(c.upload()))
    check(task['state'] == 'QUEUED', 'Task was not accepted into paused queue')
    row = c.request('POST', '/maas/v1/tasks/' + task['id'] + '/cancel').expect(200).json()
    check(row['state'] == 'CANCELLED', 'Paused queued task was not cancelled')


@case('QUOTA-001', 'quota', 'Externally saturated inference capacity returns 429',
      ('quota', 'window', 'optional'), ('inference', 'load'), ('expect_capacity_full',))
def capacity(c):
    check(c.config['expect_capacity_full'] is True, 'Requires independently confirmed occupied capacity')
    c.request('POST', '/ocr', body=c.ocr_body(), inference=True).expect(429, 'SYNC_CAPACITY')


@case('QUOTA-002', 'quota', 'Externally full queue rejects a new task',
      ('quota', 'window', 'optional'), ('write', 'inference', 'load'), ('expect_queue_full',))
def queue(c):
    check(c.config['expect_queue_full'] is True, 'Requires independently confirmed full queue')
    c.request('POST', '/maas/v1/tasks', body=c.task_body(c.upload()),
              headers={'Idempotency-Key': 'e2e-quota-' + c.run_id}).expect(429, 'QUEUE_FULL')


@case('JSON-001', 'edge', 'Task JSON envelope byte budget enforced', ('negative', 'boundary'), ('write',))
def json_limit(c):
    c.request('POST', '/maas/v1/tasks', raw=b' ' * 65537).expect(413, 'REQUEST_SIZE')
