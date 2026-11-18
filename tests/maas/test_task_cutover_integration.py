"""Forward and reverse task cutover on separate disposable PostgreSQL databases."""
from dataclasses import replace
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess

from fastapi.testclient import TestClient
import httpx
from psycopg.conninfo import conninfo_to_dict
import pytest

from maas_platform.api import create_app
from maas_platform.auth import AuthConnector
from maas_platform.config import Settings
from maas_platform.db import Database
from maas_platform.errors import ServiceError
from maas_platform.task_cutover import TaskCutover, main
from maas_platform.worker import Worker
from test_documents import pdf
from test_integration import env, headers
from test_model_onboarding_integration import DOCUMENT, unified, submit_image


@pytest.fixture
def stores(unified, tmp_path):
    new, target, settings, calls, _ = unified
    url = os.environ.get('MAAS_CUTOVER_SOURCE_TEST_DATABASE_URL')
    if not url:
        pytest.skip('Requires a second disposable PostgreSQL database')
    source_dsn, target_dsn = map(conninfo_to_dict, (url, settings.database_url))
    assert source_dsn['dbname'].endswith('_test') and source_dsn['dbname'] != target_dsn['dbname']
    catalog = tmp_path / 'original_catalog.json'
    old_model = {**settings.catalog()[DOCUMENT], 'revision': 'legacy-doc-v1'}
    catalog.write_text(json.dumps({DOCUMENT: old_model}))
    old = replace(settings, database_url=url, catalog_file=catalog, artifact_root=tmp_path/'old-artifacts')
    with Database(url).transaction() as c:
        c.execute('DROP SCHEMA IF EXISTS e17 CASCADE')
        c.execute('DROP SCHEMA public CASCADE')
        c.execute('CREATE SCHEMA public')
    with TestClient(create_app(old, upstream_auth(old)), raise_server_exceptions=False) as source:
        yield source, new, calls


def upstream_auth(settings):
    transport = httpx.MockTransport(lambda _: httpx.Response(200, json={'data': []}))
    return AuthConnector(settings, httpx.Client(transport=transport))


def upload(client):
    response = client.post('/maas/v1/artifacts', headers={**headers(), 'Content-Type': 'application/pdf'}, content=pdf(2))
    assert response.status_code == 201
    return response.json()['id']


def document(client, artifact, key, status=202):
    response = client.post('/maas/v1/tasks', headers=headers(idem=key), json={
        'model': DOCUMENT, 'input': {'document': {'artifact_id': artifact}},
    })
    assert response.status_code == status, response.json()
    return response.json()


def history(client, ident):
    path = '/maas/v1/tasks/' + ident
    result = client.get(path, headers=headers()).json()
    assert result['status'] == 'SUCCEEDED' and result['attempts'] == 1
    assert result['usage'] == {'successful_pages': 2}
    artifact = '/maas/v1/artifacts/' + result['result_artifact_id']
    response = client.get(artifact, headers=headers())
    assert response.status_code == 200 and response.json()['outputs']['page_count'] == 2
    receipt = client.get(path+'/usage', headers=headers()).json()
    assert receipt['successful_pages'] == 2 and response.json()['metering']['id'] == receipt['id']
    for private in (path, artifact, '/maas/v1/usage/'+receipt['id']):
        assert client.get(private, headers=headers('b')).status_code == 404
    return hashlib.sha256(response.content).hexdigest(), receipt


def service(client):
    return client.app.state.service


def tool(client, action, monkeypatch, manifest=None):
    monkeypatch.setattr(Settings, 'from_env', lambda: service(client).settings)
    args = [action, '--model', DOCUMENT]
    if manifest is not None:
        args.extend(['--manifest', str(manifest)])
    main(args)


def test_history_keys_and_receipts_survive_cutover_and_rollback(stores, tmp_path, monkeypatch):
    old, new, calls = stores
    old_art, new_art = upload(old), upload(new)
    first = document(old, old_art, 'legacy-page')['id']
    failed = document(old, old_art, 'legacy-failure')['id']
    expired = document(old, old_art, 'legacy-expired')['id']
    pp = submit_image(new, idem='existing-pp')
    assert Worker(service(new)).run_once()
    assert Worker(service(old)).run_once()
    task = service(old).claim()
    assert task['id'] == failed
    assert service(old).finish(task, error=ServiceError('UPSTREAM_REJECTED', 'Fixture rejection'))
    assert Worker(service(old)).run_once()
    first_proof = history(old, first)
    expired_file = old.get('/maas/v1/tasks/'+expired, headers=headers()).json()['result_artifact_id']
    with service(old).db.transaction() as c:
        c.execute("UPDATE artifacts SET expires_at=now()-interval '1 second' WHERE id=%s", (expired_file,))

    forward = tmp_path/'forward.json'
    tool(new, 'freeze', monkeypatch)
    tool(old, 'freeze', monkeypatch)
    tool(old, 'export', monkeypatch, forward)
    assert len(json.loads(forward.read_text())['keys']) == 3 and 'legacy-page' not in forward.read_text()
    tool(new, 'activate', monkeypatch, forward)
    assert document(old, old_art, 'legacy-page', 200)['id'] == first
    assert document(old, old_art, 'new-work', 503)['error']['code'] == 'MODEL_SUBMISSIONS_CLOSED'
    assert document(new, old_art, 'legacy-page', 409)['error']['code'] == 'IDEMPOTENCY_OTHER_STORE'
    assert document(new, new_art, 'legacy-page', 409)['error']['code'] == 'IDEMPOTENCY_OTHER_STORE'
    assert document(new, old_art, 'new-key-wrong-file', 404)['error']['code'] == 'INPUT_NOT_FOUND'
    assert document(new, new_art, 'legacy-failure', 409)['error']['code'] == 'IDEMPOTENCY_OTHER_STORE'
    assert history(old, first) == first_proof
    failed_response = old.get('/maas/v1/tasks/'+failed, headers=headers()).json()
    assert failed_response['status'] == 'FAILED' and failed_response['usage'] is None
    assert old.get('/maas/v1/tasks/'+failed+'/usage', headers=headers()).status_code == 404
    assert old.get('/maas/v1/artifacts/'+expired_file, headers=headers()).status_code == 410
    assert old.get('/maas/v1/tasks/'+expired+'/usage?usage_type=pages', headers=headers()).json() == {'successful_pages': 2}
    other_project = submit_image(new, idem='legacy-page', key='b')
    assert Worker(service(new)).run_once()
    assert new.get('/maas/v1/tasks/'+other_project+'/usage?usage_type=images', headers=headers('b')).json() == {
        'successful_images': 1}

    accepted = document(new, new_art, 'unified-page')['id']
    reverse = tmp_path/'reverse.json'
    tool(new, 'freeze', monkeypatch)
    with pytest.raises(ServiceError) as queued:
        TaskCutover(service(new).db).export(DOCUMENT)
    assert queued.value.code == 'MODEL_NOT_DRAINED'
    assert Worker(service(new)).run_once()
    accepted_proof = history(new, accepted)
    tool(new, 'export', monkeypatch, reverse)
    tool(old, 'activate', monkeypatch, reverse)
    assert document(old, old_art, 'unified-page', 409)['error']['code'] == 'IDEMPOTENCY_OTHER_STORE'
    assert document(new, new_art, 'unified-page', 200)['id'] == accepted
    assert document(new, new_art, 'new-work', 503)['error']['code'] == 'MODEL_SUBMISSIONS_CLOSED'
    assert old.get('/maas/v1/tasks/'+accepted, headers=headers()).status_code == 404
    assert history(new, accepted) == accepted_proof and history(old, first) == first_proof
    back = document(old, old_art, 'rollback-page')['id']
    again = submit_image(new, idem='pp-still-open')
    assert Worker(service(old)).run_once() and Worker(service(new)).run_once()
    assert not Worker(service(old)).run_once() and not Worker(service(new)).run_once()
    assert history(old, back)
    for ident in (pp, again):
        assert new.get('/maas/v1/tasks/'+ident+'/usage?usage_type=images', headers=headers()).json() == {'successful_images': 1}
    assert len(calls) == 7  # Four documents + three images. Neither cross-store retry dispatched.


def test_conflicting_project_key_leaves_destination_frozen(stores):
    old, new, _ = stores
    old_art = upload(old)
    document(old, old_art, 'not-a-conflict')
    document(old, old_art, 'collision')
    submit_image(new, idem='collision')
    source, target = TaskCutover(service(old).db), TaskCutover(service(new).db)
    source.freeze(DOCUMENT)
    target.freeze(DOCUMENT)
    for _ in range(2):
        assert Worker(service(old)).run_once()
    with pytest.raises(ServiceError) as conflict:
        target.activate(DOCUMENT, source.export(DOCUMENT))
    assert conflict.value.code == 'CUTOVER_KEY_CONFLICT'
    with service(new).db.transaction() as c:
        assert not c.execute('SELECT 1 FROM task_external_keys').fetchone()
        assert c.execute('SELECT 1 FROM task_submission_freezes WHERE model=%s', (DOCUMENT,)).fetchone()


def test_terminal_task_with_unknown_execution_still_blocks_cutover(stores):
    old, _, _ = stores
    ident = document(old, upload(old), 'uncertain')['id']
    original = service(old).claim()
    service(old).begin_backend(original)
    service(old).finish(original, error=ServiceError('TRANSPORT_TIMEOUT', 'Uncertain execution'))
    cutover = TaskCutover(service(old).db)
    cutover.freeze(DOCUMENT)
    with pytest.raises(ServiceError) as unknown:
        cutover.export(DOCUMENT)
    assert unknown.value.code == 'MODEL_NOT_DRAINED'
    service(old).complete_backend(original)
    assert [row['task_id'] for row in cutover.export(DOCUMENT)['keys']] == [ident]
    assert old.get('/maas/v1/tasks/'+ident+'/usage', headers=headers()).status_code == 404


def test_backup_restores_history_and_fence_without_restart_of_original(stores, tmp_path, monkeypatch):
    old, _, _ = stores
    uri = os.environ.get('MAAS_CUTOVER_RESTORE_TEST_DATABASE_URL')
    bin_dir = os.environ.get('MAAS_TEST_PG_BIN')
    if not uri or not bin_dir:
        pytest.skip('Requires an empty disposable restore database and PostgreSQL tools')
    original = service(old).settings
    dbname = conninfo_to_dict(uri)['dbname']
    assert dbname.endswith('_test') and dbname not in {
        conninfo_to_dict(original.database_url)['dbname'], conninfo_to_dict(os.environ['MAAS_TEST_DATABASE_URL'])['dbname']}
    art = upload(old)
    ident = document(old, art, 'snapshot-page')['id']
    assert Worker(service(old)).run_once()
    expected = history(old, ident)
    tool(old, 'freeze', monkeypatch)
    with Database(uri).transaction() as c:
        assert not c.execute("SELECT to_regclass('public.tasks') AS tasks").fetchone()['tasks']
    dump = tmp_path/'database.dump'
    subprocess.run([str(Path(bin_dir)/'pg_dump'), '-Fc', '-f', str(dump), original.database_url], check=True, timeout=30)
    subprocess.run([str(Path(bin_dir)/'pg_restore'), '--exit-on-error', '-d', uri, str(dump)], check=True, timeout=30)
    new_artifacts = tmp_path/'restored-artifacts'
    shutil.copytree(original.artifact_root, new_artifacts)
    restored = replace(original, database_url=uri, artifact_root=new_artifacts)
    with TestClient(create_app(restored, upstream_auth(restored))) as proof:
        assert history(proof, ident) == expected
        assert document(proof, art, 'snapshot-page', 200)['id'] == ident
        assert document(proof, art, 'new-key', 503)['error']['code'] == 'MODEL_SUBMISSIONS_CLOSED'
