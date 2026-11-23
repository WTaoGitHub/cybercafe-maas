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


def test_nfs_image_publish_and_rollback_preserve_default_release(setup, tmp_path):
    from test_nfs import CONFIG
    p, envelope = publisher(setup, tmp_path)
    atomic_json(p.root/'policy.json', {'schema_version':1,'allowed_cidrs':['127.0.0.1/32'],
        'grants':[{'site_id':'lab','batch_id':'pilot','cluster_id':3,'enabled':True,
                   'code_sha256':hashlib.sha256(CODE.encode()).hexdigest()}]})
    p.publish(envelope['manifest'], envelope['registration'])
    default = (p.root/'active.json').read_bytes()
    old = get(client(p.root, '127.0.0.1')).json()
    p.settings.data.update(image_id='tower-nfs', model_nfs=CONFIG)
    manifest = {**envelope['manifest'], 'config_revision':'nfs-r1',
                'compatible_image_ids':['tower-nfs'], 'model_nfs':CONFIG}
    result = p.publish(manifest, envelope['registration'], image_id='tower-nfs')
    assert result['image_id'] == 'tower-nfs' and (p.root/'active.json').read_bytes() == default
    api = client(p.root, '127.0.0.1')
    assert get(api).json() == old
    release = get(api, image='tower-nfs').json()
    assert release['manifest']['model_nfs'] == CONFIG
    assert ReleaseVerifier().verify(p.settings, release)['revision'] == 'nfs-r1'
    p.rollback('nfs-r1', 'nfs-r2', image_id='tower-nfs')
    assert (p.root/'active.json').read_bytes() == default and get(api).json() == old
    assert get(api, image='tower-nfs').json()['manifest']['config_revision'] == 'nfs-r2'
    assert api.get('/bootstrap/v1/configs/nfs-r1', headers={'Authorization':'Bearer '+CODE},
                   params={'image_id':'tower-nfs'}).status_code == 404


@pytest.mark.parametrize('image,compatible', [('../escape',['../escape']), ('tower-nfs',['tower-v1']),
                                            ('tower-v1',['tower-v1','other'])])
def test_image_publication_requires_one_matching_safe_image(setup, tmp_path, image, compatible):
    p, envelope = publisher(setup, tmp_path)
    with pytest.raises(BootstrapError, match='PUBLISH_IMAGE_SCOPE_INVALID'):
        p.publish({**envelope['manifest'], 'compatible_image_ids':compatible},
                  envelope['registration'], image_id=image)
    assert not (p.root/'active.json').exists()


def test_nfs_cannot_replace_default_configuration_by_omitting_image_scope(setup, tmp_path):
    from test_nfs import CONFIG
    p, envelope = publisher(setup, tmp_path)
    p.publish(envelope['manifest'], envelope['registration'])
    original = (p.root/'active.json').read_bytes()
    p.settings.data['model_nfs'] = CONFIG
    with pytest.raises(BootstrapError, match='PUBLISH_NFS_IMAGE_SCOPE_REQUIRED'):
        p.publish({**envelope['manifest'],'model_nfs':CONFIG,'config_revision':'nfs-r1'}, envelope['registration'])
    assert (p.root/'active.json').read_bytes() == original


def test_new_snapshot_can_change_signed_nfs_without_changing_old_release(setup, tmp_path):
    from test_nfs import CONFIG
    p, envelope = publisher(setup, tmp_path)
    atomic_json(p.root/'policy.json', {'schema_version':1, 'allowed_cidrs':['127.0.0.1/32'],
        'grants':[{'site_id':'lab', 'batch_id':'pilot', 'cluster_id':3, 'enabled':True,
                   'code_sha256':hashlib.sha256(CODE.encode()).hexdigest()}]})
    old_settings = p.settings
    old_settings.data['model_nfs'] = CONFIG
    old_manifest = dict(envelope['manifest'], model_nfs=CONFIG)
    p.publish(old_manifest, envelope['registration'], image_id='tower-v1')
    api = client(p.root, '127.0.0.1')
    old = get(api).json()
    path = tmp_path/'new-settings.json'
    data = dict(old_settings.data, image_id='tower-nfs-signed', model_nfs_from_release=True)
    del data['model_nfs']
    atomic_json(path, data)
    p.settings = LocalSettings(path)
    for revision, source in [('r2', CONFIG['source']), ('r3', 'new.example.invalid:/weights')]:
        new = dict(old_manifest, config_revision=revision, compatible_image_ids=['tower-nfs-signed'],
                   model_nfs=dict(CONFIG, source=source))
        p.publish(new, envelope['registration'], image_id='tower-nfs-signed')
        release = get(api, image='tower-nfs-signed').json()
        assert ReleaseVerifier().verify(p.settings, release)['revision'] == revision
        assert release['manifest']['model_nfs']['source'] == source
        assert get(api).json() == old
        assert ReleaseVerifier().verify(old_settings, old)['revision'] == 'r1'


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
