import json,hashlib
from pathlib import Path
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from test_client import setup,TOKEN
from maas_bootstrap.client import BootstrapError,atomic_json
from maas_bootstrap.publisher import Publisher


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
