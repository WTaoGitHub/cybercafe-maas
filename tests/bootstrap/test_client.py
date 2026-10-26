import base64,hashlib,json,os
from pathlib import Path
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from maas_bootstrap.client import BootstrapError,LocalSettings,LocalState,ReleaseVerifier,prepare,atomic_json,canonical,StaticEndpointResolver

CODE='mbc_'+'x'*40
TOKEN='native_'+('y'*40)

@pytest.fixture
def setup(tmp_path):
    key=Ed25519PrivateKey.generate()
    pub=tmp_path/'public.pem';pub.write_bytes(key.public_key().public_bytes(serialization.Encoding.PEM,serialization.PublicFormat.SubjectPublicKeyInfo))
    shared=tmp_path/'shared';shared.write_text(CODE);shared.chmod(0o600)
    cfg={'schema_version':1,'config_service_url':'https://10.11.31.1:18840','shared_code_file':str(shared),
         'tls_certificate_file':str(tmp_path/'cert.pem'),'release_public_key_file':str(pub),'release_key_id':'test-key',
         'state_dir':str(tmp_path/'state'),'worker_data_dir':str(tmp_path/'native'),'cluster_id':3,
         'model_root':str(tmp_path/'models'),'model_cache_dir':str(tmp_path/'model-cache'),
         'site_id':'lab','batch_id':'pilot','image_id':'tower-v1','architecture':'x86_64',
         'allowed_server_urls':['http://10.11.31.1:18080'],'cloud_backup':{'url':'http://configuration.cybercafe.phanlink.cn','enabled':False}}
    path=tmp_path/'bootstrap.json';atomic_json(path,cfg)
    m={'schema_version':1,'config_revision':'r1','site_id':'lab','batch_id':'pilot','cluster_id':3,
       'compatible_image_ids':['tower-v1'],'architecture':'x86_64','gpustack_version':'v2.2.3',
       'server_url':'http://10.11.31.1:18080','worker_image_digest':'quay.io/gpustack/gpustack@sha256:'+'a'*64,
       'worker_data_dir':str(tmp_path/'native'),'worker_name_policy':'native-independent','model_mounts':[],
       'cloud_backup':cfg['cloud_backup']}
    def signed(manifest=m,token=TOKEN):
        return {'manifest':json.loads(json.dumps(manifest)),'manifest_signature':{'algorithm':'Ed25519','key_id':'test-key',
                'value':base64.b64encode(key.sign(canonical(manifest))).decode()},'registration':{'cluster_id':3,'token':token}}
    return LocalSettings(path),signed

class Fetcher:
    def __init__(self,e):self.e=e;self.calls=0
    def fetch(self,s):self.calls+=1;return self.e

def test_verified_prepare_repeats_fetch_without_session_or_secret_output(setup):
    settings,sign=setup;f=Fetcher(sign())
    a=prepare(settings,f);b=prepare(settings,f)
    assert a==b and f.calls==2 and a['code']=='PREPARED'
    assert CODE not in json.dumps(a) and TOKEN not in json.dumps(a)
    state=LocalState(settings.data['state_dir']);assert state.status()['applied'] is None
    assert len(list((state.root/'generations').glob('*')))==1
    assert (state.root/'generations'/a['fingerprint']/'config.json').stat().st_mode&0o777==0o600

def test_signature_and_scope_and_url_cannot_be_overridden(setup):
    settings,sign=setup;e=sign();e['manifest']['cluster_id']=1
    with pytest.raises(BootstrapError,match='RELEASE_VERIFICATION_FAILED'):ReleaseVerifier().verify(settings,e)
    m={**sign()['manifest'],'server_url':'http://attacker.invalid:80'}
    with pytest.raises(BootstrapError,match='RELEASE_TARGET_NOT_ALLOWED'):ReleaseVerifier().verify(settings,sign(m))
    m={**sign()['manifest'],'site_id':'other'}
    with pytest.raises(BootstrapError,match='RELEASE_SCOPE_MISMATCH'):ReleaseVerifier().verify(settings,sign(m))

def test_failed_verification_preserves_last_prepared(setup):
    settings,sign=setup;ok=prepare(settings,Fetcher(sign()));bad=sign();bad['manifest_signature']['value']='bad'
    with pytest.raises(BootstrapError):prepare(settings,Fetcher(bad))
    assert LocalState(settings.data['state_dir']).read('prepared.json')['fingerprint']==ok['fingerprint']

def test_native_token_rotation_changes_fingerprint_even_same_revision(setup):
    settings,sign=setup
    a=ReleaseVerifier().verify(settings,sign());b=ReleaseVerifier().verify(settings,sign(token='other_'+('z'*40)))
    assert a['revision']==b['revision'] and a['fingerprint']!=b['fingerprint']

def test_lock_busy_prevents_concurrent_apply(setup):
    settings,sign=setup
    with LocalState(settings.data['state_dir']).lock():
        with pytest.raises(BootstrapError,match='BOOTSTRAP_BUSY'):prepare(settings,Fetcher(sign()))

def test_symlink_and_overbroad_permissions_rejected(setup,tmp_path):
    settings,sign=setup;p=Path(settings.data['shared_code_file']);p.chmod(0o644)
    from maas_bootstrap.client import SharedCodeFileProvider
    with pytest.raises(BootstrapError,match='LOCAL_FILE_PERMISSIONS'):SharedCodeFileProvider().get(settings)
    p.unlink();p.symlink_to(tmp_path/'bootstrap.json')
    with pytest.raises(BootstrapError):SharedCodeFileProvider().get(settings)

def test_unsupported_dynamic_options_and_cloud_fail_closed(setup):
    settings,sign=setup;m={**sign()['manifest'],'model_mounts':[{'source':'/','dest':'/host'}]}
    with pytest.raises(BootstrapError,match='RELEASE_OPTIONS_UNSUPPORTED'):ReleaseVerifier().verify(settings,sign(m))
    settings.data['cloud_backup']['enabled']=True
    with pytest.raises(BootstrapError,match='CLOUD_BACKUP_UNSUPPORTED'):StaticEndpointResolver().resolve(settings)
