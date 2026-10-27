from ..core import case, check


@case('AUTH-001', 'auth', 'Missing Bearer is rejected', ('smoke', 'negative', 'auth'))
def missing(c):
    c.request('GET', '/maas/v1/models/' + c.model + '/capabilities', identity=None).expect(401, 'UNAUTHENTICATED')


@case('AUTH-002', 'auth', 'Unregistered key is rejected', ('smoke', 'negative', 'auth'))
def invalid(c):
    c.request('GET', '/maas/v1/models/' + c.model + '/capabilities', identity='invalid').expect(401, 'UNAUTHENTICATED')


@case('AUTH-003', 'auth', 'Registered customer key and model authorization', ('smoke', 'positive', 'auth'))
def valid(c):
    r = c.request('GET', '/maas/v1/models/' + c.model + '/capabilities').expect(200)
    check(r.json()['model'] == c.model, 'Wrong logical model')


@case('AUTH-004', 'auth', 'Identity headers cannot bypass authentication', ('negative', 'auth', 'isolation'))
def spoof(c):
    c.request('GET', '/maas/v1/models/' + c.model + '/capabilities', identity=None,
              headers={'X-Project': 'pilot-ocr', 'X-User-ID': 'admin'}).expect(401, 'UNAUTHENTICATED')


@case('AUTH-005', 'auth', 'Restricted credential cannot access OCR', ('negative', 'auth', 'optional'),
      requires=('restricted_key_file',))
def restricted(c):
    c.request('GET', '/maas/v1/models/' + c.model + '/capabilities', identity='restricted').expect(403)


@case('CAP-001', 'capabilities', 'Published async input and budget schema', ('smoke', 'contract'))
def schema(c):
    value = c.request('GET', '/maas/v1/models/' + c.model + '/capabilities').expect(200).json()
    check(value['model'] == c.model and bool(value['revision']), 'Missing model/revision')
    check(value['execution_modes'] == ['async'], 'Current capabilities describe async adapter only')
    check(0 < value['max_runtime_s'] <= 3600, 'Invalid runtime budget')
    check(value['input_schema']['type'] == 'object' and value['parameters_schema']['type'] == 'object',
          'Invalid input/parameter schema')
    if c.config.get('expected_revision'):
        check(value['revision'] == c.config['expected_revision'], 'Unexpected deployed model revision')


@case('CAP-002', 'capabilities', 'Unknown model is not implicitly aliased', ('negative', 'contract'))
def unknown(c):
    c.request('GET', '/maas/v1/models/e2e-absent-model/capabilities').expect(404, 'MODEL_NOT_FOUND')


@case('EDGE-001', 'edge', 'API health and OpenAPI are not customer routes', ('smoke', 'boundary'))
def internal(c):
    for path in ('/healthz', '/docs', '/openapi.json', '/v1/models'):
        c.request('GET', path).expect(404)


@case('EDGE-002', 'edge', 'Unsupported generation and batch routes are closed', ('boundary', 'negative'))
def unsupported(c):
    for path in ('/images/generations', '/v1/images/generations', '/maas/v1/batches'):
        c.request('GET', path).expect(404)


@case('EDGE-003', 'edge', 'Query parameters rejected at business ingress', ('boundary', 'negative'))
def query(c):
    c.request('GET', '/maas/v1/models/' + c.model + '/capabilities?unexpected=1').expect(400)


@case('EDGE-004', 'edge', 'Generated request ID and safe JSON error envelope', ('smoke', 'contract'))
def error_contract(c):
    r = c.request('GET', '/maas/v1/tasks/task_e2e_absent').expect(404, 'NOT_FOUND')
    error = r.json()['error']
    check(isinstance(error['message'], str) and isinstance(error['retryable'], bool), 'Invalid error fields')
    check(error['request_id'] == r.headers.get('x-request-id')
          and error['request_id'].startswith('req_'), 'Missing or inconsistent server request ID')


@case('EDGE-005', 'edge', 'Client request ID is not trusted as server ID', ('contract', 'negative'))
def client_id(c):
    r = c.request('GET', '/maas/v1/models/' + c.model + '/capabilities',
                  headers={'X-Request-ID': 'e2e-client-controlled'}).expect(200)
    check(r.headers.get('x-request-id', '').startswith('req_'), 'Missing generated request ID')
    check(r.headers['x-request-id'] != 'e2e-client-controlled', 'Client controlled server request ID')


@case('EDGE-006', 'edge', 'Unsupported method denied by ingress', ('boundary', 'negative'), ('write',))
def method(c):
    c.request('DELETE', '/maas/v1/tasks/task_e2e_absent').expect(403)
