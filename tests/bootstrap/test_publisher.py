import json,hashlib
from pathlib import Path
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from test_client import setup,TOKEN
from maas_bootstrap.client import BootstrapError,LocalSettings,ReleaseVerifier,atomic_json
from maas_bootstrap.publisher import Publisher
from test_service import CODE,client,get


def publisher(setup,tmp_path):
    settings,sign=setup;key=Ed25519PrivateKey.generate()
    Path(settings.data['release_public_key_file']).write_bytes(key.public_key().public_bytes(serialization.Encoding.PEM,serialization.PublicFormat.SubjectPublicKeyInfo))
    p=tmp_path/'key';p.write_bytes(key.private_bytes(serialization.Encoding.PEM,serialization.PrivateFormat.PKCS8,serialization.NoEncryption()));p.chmod(0o600)
    root=tmp_path/'private';root.mkdir(mode=0o700)
    atomic_json(root/'policy.json',{'grants':[{'site_id':'lab','batch_id':'pilot','cluster_id':3}]})
    return Publisher(root,settings,p),sign()


def test_immutable_publish_and_rollback_as_new_revision(setup,tmp_path):
    p,e=publisher(setup,tmp_path)
    a=p.publish(e['manifest'],e['registration']);assert TOKEN not in json.dumps(a)
    assert p.publish(e['manifest'],e['registration'])==a
    pointer=json.loads((p.root/'active.json').read_text())
    raw=(p.root/'releases/r1.json').read_bytes()
    assert pointer['sha256']==hashlib.sha256(raw).hexdigest()
    with pytest.raises(BootstrapError,match='REVISION_IMMUTABLE'):
        p.publish(e['manifest'],{'cluster_id':3,'token':'new-token-'+'a'*30})
    assert json.loads((p.root/'active.json').read_text())==pointer
    p.publish({**e['manifest'],'config_revision':'r2'},e['registration'])
    result=p.rollback('r1','r3');assert result['rollback_of']=='r1' and not result['worker_actions']
    assert json.loads((p.root/'active.json').read_text())['revision']=='r3'
    assert (p.root/'releases/r1.json').read_bytes()==raw


def test_bad_publish_leaves_active_and_releases_unchanged(setup,tmp_path):
    p,e=publisher(setup,tmp_path);p.publish(e['manifest'],e['registration'])
    previous=(p.root/'active.json').read_bytes()
    with pytest.raises(BootstrapError):
        p.publish({**e['manifest'],'config_revision':'r2','server_url':'http://other.invalid'},e['registration'])
    assert (p.root/'active.json').read_bytes()==previous and not (p.root/'releases/r2.json').exists()
    with pytest.raises(BootstrapError):p.rollback('../policy','r3')


@pytest.mark.parametrize('explicit_cluster', [None, 3])
@pytest.mark.parametrize('diskless', [False, True])
def test_published_cluster_defaults_to_settings_and_is_served(setup, tmp_path, explicit_cluster, diskless):
    p, envelope = publisher(setup, tmp_path)
    path = tmp_path/'bootstrap.json'
    settings = json.loads(path.read_text())
    grant = {'site_id':'lab','batch_id':'pilot','code_sha256':hashlib.sha256(CODE.encode()).hexdigest(),
             'enabled':True}
    if explicit_cluster is None:
        del settings['cluster_id']
    else:
        settings['cluster_id'] = grant['cluster_id'] = explicit_cluster
    manifest, registration = envelope['manifest'], envelope['registration']
    del manifest['cluster_id'], registration['cluster_id']
    if diskless:
        native_url = 'https://native.example.invalid:443'
        settings.update(allowed_server_urls=[native_url], worker_start_grant_file=str(tmp_path/'boot-grant'))
        manifest.update(server_url=native_url, worker_name_policy='gpu-external-id')
        del registration['token']
    atomic_json(path, settings)
    p.settings = LocalSettings(path)
    atomic_json(p.root/'policy.json', {'schema_version':1,'allowed_cidrs':['127.0.0.1/32'],'grants':[grant]})

    cluster_id = 1 if explicit_cluster is None else explicit_cluster
    assert p.publish(manifest, registration)['cluster_id'] == cluster_id
    response = get(client(p.root, '127.0.0.1'))
    assert response.status_code == 200
    release = response.json()
    assert release['manifest']['cluster_id'] == release['registration']['cluster_id'] == cluster_id
    assert ReleaseVerifier().verify(p.settings, release)['cluster_id'] == cluster_id
    assert 'cluster_id' not in manifest and 'cluster_id' not in registration

    with pytest.raises(BootstrapError, match='RELEASE_SCOPE_MISMATCH'):
        p.publish(dict(manifest, cluster_id=2, config_revision='r2'), registration)
    with pytest.raises(BootstrapError, match='RELEASE_SCOPE_MISMATCH'):
        p.publish(dict(manifest, config_revision='r2'), dict(registration, cluster_id=True))
