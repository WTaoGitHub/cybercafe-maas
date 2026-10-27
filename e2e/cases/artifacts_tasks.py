import hashlib
from ..core import case, check


@case('ART-001', 'artifacts', 'Upload metadata and exact authenticated download', ('positive', 'storage', 'smoke'), ('write',))
def roundtrip(c):
    raw = c.image()
    ident = c.upload(raw)
    r = c.request('GET', '/maas/v1/artifacts/' + ident).expect(200)
    check(r.raw == raw, 'Downloaded bytes differ from uploaded fixture')
    check(r.headers.get('cache-control') == 'no-store'
          and r.headers.get('content-disposition') == 'attachment', 'Missing download privacy headers')


@case('ART-002', 'artifacts', 'Empty upload rejected', ('negative', 'storage'), ('write',))
def empty(c):
    c.request('POST', '/maas/v1/artifacts', raw=b'', media='image/png').expect(413, 'INPUT_SIZE')


@case('ART-003', 'artifacts', 'Unsupported media type rejected', ('negative', 'storage'), ('write',))
def media(c):
    c.request('POST', '/maas/v1/artifacts', raw=b'x', media='text/html').expect(415, 'MEDIA_TYPE')


@case('ART-004', 'artifacts', 'Missing artifact returns safe 404', ('negative', 'storage'))
def absent(c):
    c.request('GET', '/maas/v1/artifacts/art_e2e_absent').expect(404, 'NOT_FOUND')


@case('ART-005', 'artifacts', 'Duplicate upload creates separate IDs', ('positive', 'storage'), ('write',))
def duplicates(c):
    a, b = c.upload(), c.upload()
    check(a != b, 'Upload incorrectly treated as idempotent')


@case('ART-006', 'artifacts', 'Upload byte limit enforced', ('negative', 'boundary', 'large'), ('write', 'load'))
def byte_limit(c):
    limit = c.config.get('max_input_bytes', 10 * 1024 * 1024)
    check(0 < limit <= 20 * 1024 * 1024, 'Invalid configured upload limit')
    c.request('POST', '/maas/v1/artifacts', raw=b'x' * (limit + 1),
              media='application/octet-stream').expect(413)


@case('TASK-001', 'tasks', 'Upload-submit-poll-download real async OCR', ('positive', 'gpu', 'smoke', 'async'),
      ('write', 'inference'))
def async_roundtrip(c):
    task, _ = c.submit(c.task_body(c.upload()))
    row = c.wait(task['id'])
    check(row['state'] == 'SUCCEEDED', 'Async OCR did not succeed')
    result = c.request('GET', '/maas/v1/artifacts/' + row['result_artifact_id']).expect(200).json()
    check(isinstance(result['outputs']['lines'], list), 'Missing async OCR lines')
    check(result['model_revision'] == row['revision'] and result['usage'] == {'unit': 'task', 'quantity': 1},
          'Async result revision/usage contract differs')
    check(result['attempt'] == row['attempts'], 'Result attempt mismatch')
    c.current['observations'] = {'task_id': task['id'], 'result_artifact_id': row['result_artifact_id'],
                               'line_count': len(result['outputs']['lines'])}


@case('TASK-002', 'tasks', 'Same key/body reuses same task without new execution', ('positive', 'idempotency', 'async'),
      ('write', 'inference'))
def idem(c):
    body = c.task_body(c.upload())
    task, key = c.submit(body)
    r = c.request('POST', '/maas/v1/tasks', body=body, headers={'Idempotency-Key': key}).expect(200)
    check(r.json()['id'] == task['id'], 'Idempotency returned a different task')
    row = c.wait(task['id'])
    check(row['state'] == 'SUCCEEDED', 'Original idempotent task did not succeed')
    r = c.request('POST', '/maas/v1/tasks', body=body, headers={'Idempotency-Key': key}).expect(200)
    check(r.json()['id'] == task['id'] and r.json()['state'] == 'SUCCEEDED', 'Terminal task not reused')


@case('TASK-003', 'tasks', 'Same key and changed request conflicts', ('negative', 'idempotency', 'async'),
      ('write', 'inference'))
def conflict(c):
    body = c.task_body(c.upload())
    task, key = c.submit(body)
    c.request('POST', '/maas/v1/tasks', body={**body, 'timeout_seconds': 121},
              headers={'Idempotency-Key': key}).expect(409, 'IDEMPOTENCY_CONFLICT')
    check(c.wait(task['id'])['state'] == 'SUCCEEDED', 'Original task did not succeed')


@case('TASK-004', 'tasks', 'Missing idempotency header rejected', ('negative', 'idempotency'), ('write',))
def no_key(c):
    c.request('POST', '/maas/v1/tasks', body=c.task_body('art_e2e_absent')).expect(400, 'IDEMPOTENCY_REQUIRED')


@case('TASK-005', 'tasks', 'Missing artifact is not accepted', ('negative', 'async'), ('write',))
def missing_input(c):
    c.request('POST', '/maas/v1/tasks', body=c.task_body('art_e2e_absent'),
              headers={'Idempotency-Key': 'e2e-missing-' + c.run_id}).expect(404, 'INPUT_NOT_FOUND')


@case('TASK-006', 'tasks', 'Extra fields and invalid retry budgets rejected', ('negative', 'contract'), ('write',))
def schema(c):
    for change in ({'extra': True}, {'max_attempts': 4}, {'timeout_seconds': 0}):
        c.request('POST', '/maas/v1/tasks', body={**c.task_body('art_e2e_absent'), **change},
                  headers={'Idempotency-Key': 'e2e-schema-' + c.run_id}).expect(422, 'INPUT_INVALID')


@case('TASK-007', 'tasks', 'OCR input and parameter schema enforced', ('negative', 'contract'), ('write',))
def input_schema(c):
    for change in ({'input': {'image_url': 'https://example.org/a.png'}}, {'parameters': {'prompt': 'x'}}):
        c.request('POST', '/maas/v1/tasks', body={**c.task_body('art_e2e_absent'), **change},
                  headers={'Idempotency-Key': 'e2e-input-' + c.run_id}).expect(422, 'INPUT_INVALID')


@case('TASK-008', 'tasks', 'Missing task cannot be queried or cancelled', ('negative', 'async'), ('write',))
def absent_task(c):
    c.request('GET', '/maas/v1/tasks/task_e2e_absent').expect(404, 'NOT_FOUND')
    c.request('POST', '/maas/v1/tasks/task_e2e_absent/cancel').expect(404, 'NOT_FOUND')


@case('TASK-009', 'tasks', 'Cancellation is race-aware and repeatable', ('positive', 'cancellation', 'async'),
      ('write', 'inference'))
def cancel(c):
    task, _ = c.submit(c.task_body(c.upload()))
    r = c.request('POST', '/maas/v1/tasks/' + task['id'] + '/cancel').expect(200).json()
    check(r['state'] in ('CANCELLED', 'CANCEL_REQUESTED', 'SUCCEEDED', 'FAILED'), 'Invalid cancel state')
    row = c.wait(task['id'])
    repeated = c.request('POST', '/maas/v1/tasks/' + task['id'] + '/cancel').expect(200).json()
    check(repeated['state'] == row['state'], 'Cancel changed a terminal state')
    check(repeated['id'] == task['id'], 'Cancel created a different task')


@case('ISO-001', 'isolation', 'Different project cannot download known input or spoof ownership',
      ('negative', 'isolation', 'optional'), ('write',), ('secondary_key_file', 'distinct_projects'))
def artifact_isolation(c):
    check(c.config['distinct_projects'] is True, 'Set distinct_projects=true only after verifying identities')
    ident = c.upload()
    c.request('GET', '/maas/v1/artifacts/' + ident, identity='secondary').expect(404, 'NOT_FOUND')
    c.request('GET', '/maas/v1/artifacts/' + ident, identity='secondary',
              headers={'X-Project': c.config.get('project', 'pilot-ocr')}).expect(404, 'NOT_FOUND')


@case('ISO-002', 'isolation', 'Different project cannot query/cancel task or download result',
      ('negative', 'isolation', 'optional', 'async'), ('write', 'inference'),
      ('secondary_key_file', 'distinct_projects'))
def task_isolation(c):
    check(c.config['distinct_projects'] is True, 'distinct_projects must be true')
    task, _ = c.submit(c.task_body(c.upload()))
    path = '/maas/v1/tasks/' + task['id']
    c.request('GET', path, identity='secondary').expect(404, 'NOT_FOUND')
    c.request('POST', path + '/cancel', identity='secondary').expect(404, 'NOT_FOUND')
    row = c.wait(task['id'])
    check(row['state'] == 'SUCCEEDED', 'Async task did not succeed')
    c.request('GET', '/maas/v1/artifacts/' + row['result_artifact_id'], identity='secondary').expect(404, 'NOT_FOUND')


@case('TTL-001', 'lifecycle', 'Previously expired own artifact is unavailable', ('retention', 'optional'),
      requires=('expired_artifact_id',))
def expired(c):
    ident = c.config['expired_artifact_id']
    check(ident.startswith('art_') and ident.replace('_', '').isalnum(), 'Invalid fixture artifact ID')
    c.request('GET', '/maas/v1/artifacts/' + ident).expect(410, 'RESULT_EXPIRED')
