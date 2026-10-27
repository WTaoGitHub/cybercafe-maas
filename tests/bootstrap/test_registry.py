import copy,datetime,hashlib,json
from pathlib import Path
import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes,serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID
from maas_bootstrap import registry,worker
from maas_bootstrap.client import BootstrapError,ReleaseVerifier,canonical
from test_client import setup
from test_storage_layout import image

@pytest.fixture
def access(setup):
 s,sign=setup;s.data['allowed_registry_endpoints']=['10.11.31.1:18443']
 k=rsa.generate_private_key(public_exponent=65537,key_size=2048);name=x509.Name([x509.NameAttribute(NameOID.COMMON_NAME,'test-registry')])
 now=datetime.datetime.now(datetime.timezone.utc)
 cert=x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(k.public_key()).serial_number(1).not_valid_before(now-datetime.timedelta(days=1)).not_valid_after(now+datetime.timedelta(days=1)).add_extension(x509.BasicConstraints(ca=True,path_length=0),critical=True).sign(k,hashes.SHA256())
 payload={'certificate_pem':cert.public_bytes(serialization.Encoding.PEM).decode(),'username':'reader','password':'r'*40}
 m={**sign()['manifest'],'registry_access':{'schema_version':1,'endpoint':'10.11.31.1:18443','credential_id':'reader-r1','payload_sha256':hashlib.sha256(canonical(payload)).hexdigest()}}
 e=sign(m);e['registration']['registry_access']=payload
 return s,e,sign

def test_signed_payload_and_endpoint_scoped_auth(access,tmp_path):
 s,e,_=access;metadata=ReleaseVerifier().verify(s,e);trust=tmp_path/'trust'
 registry.prepare(s,e,trust);registry.prepare(s,e,trust)
 spec=registry.validate(s,e);p=Path(spec['auth_dir'])/'config.json'
 assert p.stat().st_mode&0o777==0o600 and p.parent.stat().st_mode&0o777==0o700
 assert set(json.loads(p.read_text())['auths'])=={'10.11.31.1:18443'}
 assert 'quay.io' not in p.read_text()
 assert e['registration']['registry_access']['password'] not in json.dumps(metadata)
 assert (trust/'10.11.31.1:18443/ca.crt').read_text()==e['registration']['registry_access']['certificate_pem']

@pytest.mark.parametrize('kind',['password','certificate','endpoint','unsigned'])
def test_altered_or_unapproved_payload_rejected(access,kind):
 s,e,sign=access
 if kind=='password':e['registration']['registry_access']['password']='evil'*10
 elif kind=='certificate':e['registration']['registry_access']['certificate_pem']='not a certificate'
 elif kind=='endpoint':
  m=copy.deepcopy(e['manifest']);m['registry_access']['endpoint']='evil.example:443';r=sign(m);r['registration']['registry_access']=e['registration']['registry_access'];e=r
 else:e['manifest'].pop('registry_access');e['manifest_signature']=sign()['manifest_signature']
 with pytest.raises(BootstrapError):ReleaseVerifier().verify(s,e)
 assert not Path(s.data['state_dir']).exists()

@pytest.mark.parametrize('kind',['trust','auth','auth-mode','auth-symlink'])
def test_existing_trust_auth_and_permissions_not_silently_overwritten(access,tmp_path,kind):
 s,e,_=access;trust=tmp_path/'trust';registry.prepare(s,e,trust)
 auth=Path(registry.validate(s,e)['auth_dir'])/'config.json'
 if kind=='trust':(trust/'10.11.31.1:18443/ca.crt').write_text('different anchor')
 elif kind=='auth':auth.write_text('{"auths":{}}')
 elif kind=='auth-mode':auth.chmod(0o644)
 else:
  auth.unlink();auth.symlink_to(tmp_path/'missing')
 with pytest.raises(BootstrapError):registry.prepare(s,e,trust)


def test_layout_has_no_secret_env_and_excludes_auth_mount(access,monkeypatch):
 s,e,_=access;image(monkeypatch);spec=worker.NativeDockerWorker().desired_layout(s,e)
 assert spec['version']=='registry-access-v1'
 assert spec['env']['DOCKER_CONFIG']==registry.TARGET
 assert registry.TARGET in spec['env']['GPUSTACK_RUNTIME_DEPLOY_MIRRORED_DEPLOYMENT_IGNORE_VOLUMES'].split(';')
 m=next(m for m in spec['mounts'] if m['target']==registry.TARGET);assert m['rw'] is False
 assert e['registration']['registry_access']['password'] not in json.dumps(spec)
 assert not any('PASSWORD' in key or 'USERNAME' in key for key in spec['env'])
