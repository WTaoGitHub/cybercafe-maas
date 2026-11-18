"""Exercise dispatch-time fencing, late evidence and legacy rows on PostgreSQL."""
import json

import pytest

from maas_platform.errors import ServiceError
from maas_platform.sync_ingress import SyncIngress
from test_integration import env, submit


def test_same_route_quarantine_defers_already_claimed_async_alias(env):
    client, service, settings, auth, _ = env
    settings.max_project_running = 2
    catalog = settings.catalog()
    catalog['mock.echo']['upstream_model'] = 'one-backend'
    catalog['mock.alias'] = dict(catalog['mock.echo'])
    settings.catalog_file.write_text(json.dumps(catalog))
    bindings = json.loads(settings.bindings_file.read_text())
    bindings['credentials'][0]['models'].append('mock.alias')
    settings.bindings_file.write_text(json.dumps(bindings))
    assert submit(client, 'first').status_code == 202
    alias = submit(client, 'alias', model='mock.alias').json()['id']
    original, preparing = service.claim(), service.claim()
    assert preparing['id'] == alias
    service.begin_backend(original)
    service.finish(original, error=ServiceError('TRANSPORT_TIMEOUT', 'Uncertain execution'))
    with pytest.raises(ServiceError) as deferred:
        service.begin_backend(preparing)
    assert deferred.value.code == 'DISPATCH_DEFERRED'
    assert service.claim() is None
    with service.db.transaction() as c:
        assert c.execute('SELECT count(*) AS n FROM backend_activity').fetchone()['n'] == 1
        attempt = c.execute('SELECT state, error_code FROM task_attempts WHERE token=%s', (preparing['token'],)).fetchone()
        assert attempt == {'state': 'DEFERRED', 'error_code': 'BACKEND_QUARANTINED'}
        c.execute('UPDATE tasks SET next_run=clock_timestamp() WHERE id=%s', (alias,))
    service.complete_backend(original)
    assert service.claim()['id'] == alias


@pytest.mark.parametrize('legacy', [False, True])
def test_sync_late_terminal_evidence_releases_only_its_own_token(env, legacy):
    client, service, settings, auth, _ = env
    a, b = [auth.authenticate('gpustack_registered_' + key) for key in ('a', 'b')]
    catalog = settings.catalog()
    catalog['mock.other'] = dict(catalog['mock.echo'])
    settings.catalog_file.write_text(json.dumps(catalog))
    ingress = SyncIngress(service)
    uncertain = ingress.begin(a, 'mock.echo', 30)
    with service.db.transaction() as c:
        c.execute("UPDATE sync_activity SET deadline=now()-interval '1 second' WHERE token=%s", (uncertain,))
        if legacy:
            c.execute('UPDATE sync_activity SET dispatch_route=NULL WHERE token=%s', (uncertain,))
    with pytest.raises(ServiceError) as blocked:
        ingress.begin(b, 'mock.echo', 30)
    assert blocked.value.code == 'BACKEND_QUARANTINED'
    ingress.finish('unrelated-token', True)
    if legacy:
        with pytest.raises(ServiceError, match='blocks this inference scope'):
            ingress.begin(b, 'mock.other', 30)
    else:
        other = ingress.begin(b, 'mock.other', 30)
        ingress.finish(other, True)
    assert service.metrics()['unknown_sync_activity'] == 1
    ingress.finish(uncertain, True)
    assert ingress.begin(b, 'mock.echo', 30)
    with service.db.transaction() as c:
        assert c.execute('SELECT resolution_reason FROM sync_activity WHERE token=%s', (uncertain,)).fetchone() == {
            'resolution_reason': 'Transport confirmed completion'}
        assert c.execute('SELECT count(*) AS n FROM sync_usage_events').fetchone()['n'] == 0
