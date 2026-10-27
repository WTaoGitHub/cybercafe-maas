from ..core import case, check
from ..observer import observe
from .ocr import lines


@case('OPS-001', 'operations', 'Current control plane components running', ('operations', 'readonly', 'optional'),
      requires=('operator',))
def components(c):
    rows = observe(c, 'components')['containers']
    names = {r['Names'] for r in rows}
    required = {'maas-tasks-api-1', 'maas-tasks-executor-1', 'maas-tasks-observer-1',
                'maas-tasks-db-1', 'maas-edge', 'maas-private-tls', 'wta-gpustack-server'}
    check(required <= names, 'A required control-plane container is not running')
    c.current['observations'] = {'required_components_running': sorted(required)}


@case('OPS-002', 'operations', 'Business monitor fresh, healthy and no UNKNOWN', ('operations', 'readonly', 'optional'),
      requires=('operator',))
def monitor(c):
    value = observe(c, 'monitor')
    c.current['observations'] = value
    check(-5 <= value['age_s'] <= c.config.get('monitor_max_age_s', 120), 'Monitor evidence is stale')
    check(value['ok'] is True and value['alerts'] == 0, 'Business monitor has alerts')
    check(value['unknown_backend'] == value['unknown_sync'] == 0, 'UNKNOWN activity is unresolved')


@case('OPS-003', 'operations', 'Retired collector remains inactive and masked', ('operations', 'readonly', 'optional'),
      requires=('operator',))
def collector(c):
    value = observe(c, 'collector_retired')
    check(value['active'] == 'inactive' and value['unit_state'] in ('masked', 'masked-runtime'),
          'Retired collector must stay inactive and masked; framework will not restore it')


@case('E5-001', 'bootstrap', 'Configuration and authenticated registry components running',
      ('e5', 'operations', 'readonly', 'optional'), requires=('operator',))
def bootstrap(c):
    names = {r['Names'] for r in observe(c, 'components')['containers']}
    check({'maas-bootstrap-config', 'maas-registry-tls', 'maas-registry-store'} <= names,
          'Configuration/registry service is not running')
    # Only server-side readiness, NOT a claim of clone first-boot or driver verification.


@case('METER-001', 'metering', 'One successful async task creates exactly one task event',
      ('metering', 'gpu', 'async', 'optional'), ('write', 'inference'), ('operator',))
def task_event(c):
    row, _ = c.submit(c.task_body(c.upload()))
    check(c.wait(row['id'])['state'] == 'SUCCEEDED', 'Task did not succeed')
    value = observe(c, 'task_usage', task_id=row['id'])
    check(value['success_events'] == 1, 'Expected exactly one usage event for this task')
    c.current['observations'] = {'task_id': row['id'], **value}


@case('METER-002', 'metering', 'Successful sync increments exclusive project event count once',
      ('metering', 'gpu', 'exclusive', 'optional'), ('inference',), ('operator', 'project', 'exclusive_project'))
def sync_event(c):
    check(c.config['exclusive_project'] is True, 'Requires an exclusively used test project')
    before = observe(c, 'sync_usage', project=c.config['project'])['success_events']
    lines(c.request('POST', '/ocr', body=c.ocr_body(), inference=True).expect(200).json())
    after = observe(c, 'sync_usage', project=c.config['project'])['success_events']
    check(after - before == 1, 'Unexpected event delta; verify configured project and exclusive use')
    c.current['observations'] = {'event_delta': after-before}


@case('METER-003', 'metering', 'Invalid image creates no successful sync event',
      ('metering', 'negative', 'exclusive', 'optional'), ('write',), ('operator', 'project', 'exclusive_project'))
def failed_event(c):
    check(c.config['exclusive_project'] is True, 'Requires an exclusively used test project')
    before = observe(c, 'sync_usage', project=c.config['project'])['success_events']
    c.request('POST', '/ocr', body=c.ocr_body(image_base64='invalid!')).expect(422, 'IMAGE_BASE64')
    after = observe(c, 'sync_usage', project=c.config['project'])['success_events']
    check(after == before, 'Invalid input unexpectedly increased successful event count')
