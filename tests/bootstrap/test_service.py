import hashlib
import json
from pathlib import Path
from fastapi.testclient import TestClient
import pytest
from maas_bootstrap.service import create_app, BoundedLimiter

CODE = 'test-code-only-' + 'x' * 40
NATIVE = 'native-test-not-a-real-secret'

@pytest.fixture
def state(tmp_path):
    policy = {'schema_version': 1, 'allowed_cidrs': ['127.0.0.0/8', '10.11.31.0/24', '10.12.0.0/22'],
              'grants': [{'code_id':'test', 'code_sha256':hashlib.sha256(CODE.encode()).hexdigest(),
                          'enabled':True, 'expires_at':None, 'site_id':'cafe', 'batch_id':'pilot', 'cluster_id':3}]}
    envelope = {'manifest':{'schema_version':1, 'config_revision':'r1', 'site_id':'cafe', 'batch_id':'pilot',
                 'cluster_id':3, 'compatible_image_ids':['tower-v1']},
                'manifest_signature':{'algorithm':'Ed25519','value':'dummy-test-signature'},
                'registration':{'cluster_id':3,'token':NATIVE}}
    (tmp_path/'releases').mkdir()
    publish(tmp_path, envelope)
    (tmp_path/'policy.json').write_text(json.dumps(policy))
    return tmp_path

def publish(root, value):
    raw=json.dumps(value).encode();(root/'releases/r1.json').write_bytes(raw)
    (root/'active.json').write_text(json.dumps({'revision':'r1','sha256':hashlib.sha256(raw).hexdigest()}))

def client(state, ip='10.11.31.3', **kw):
    return TestClient(create_app(state, **kw), client=(ip, 12345))

def get(c, code=CODE, image='tower-v1'):
    return c.get('/bootstrap/v1/config', headers={'Authorization':'Bearer '+code}, params={'image_id':image})

def edit(root, fn):
    p=root/'policy.json';d=json.loads(p.read_text());fn(d);p.write_text(json.dumps(d))

def test_same_configuration_two_peers_no_sessions(state):
    a=get(client(state));b=get(client(state,'10.12.1.4'))
    assert a.status_code==b.status_code==200 and a.json()==b.json()
    assert a.json()['registration']['token']==NATIVE
    assert a.headers['cache-control']=='no-store'
    assert client(state).post('/bootstrap/v1/sessions').status_code==404
    assert client(state).get('/docs').status_code==404

def test_auth_scope_and_expiry_fail_closed_without_secret(state):
    c=client(state)
    assert c.get('/bootstrap/v1/config').status_code==401
    assert get(c,'bad').status_code==401
    edit(state,lambda d:d['grants'][0].update(enabled=False))
    assert get(c).status_code==403
    edit(state,lambda d:d['grants'][0].update(enabled=True,expires_at=1))
    assert get(c).status_code==403
    edit(state,lambda d:d['grants'][0].update(expires_at=None,cluster_id=1))
    r=get(c);assert r.status_code==403 and NATIVE not in r.text and CODE not in r.text
    edit(state,lambda d:d['grants'][0].pop('cluster_id'))
    assert get(c).status_code==403

def test_no_x_forwarded_for_bypass_and_multiple_cidrs(state):
    r=client(state,'192.0.2.2').get('/healthz',headers={'X-Forwarded-For':'10.11.31.3'})
    assert r.status_code==403
    assert client(state,'10.12.3.254').get('/healthz').status_code==200
    assert client(state,'10.12.4.1').get('/healthz').status_code==403

def test_integrity_pointer_and_compatibility(state):
    c=client(state);assert get(c,image='unknown').status_code==409
    assert c.get('/bootstrap/v1/config',headers={'Authorization':'Bearer '+CODE}).status_code==400
    assert c.get('/bootstrap/v1/configs/r0',headers={'Authorization':'Bearer '+CODE}).status_code==404
    (state/'releases/r1.json').write_text('not json '+NATIVE)
    r=get(c);assert r.status_code==503 and NATIVE not in r.text
    (state/'active.json').write_text('{"revision":"../../secret","sha256":"x"}')
    assert get(c).status_code==503

def test_atomic_policy_reload_revokes_next_read(state):
    c=client(state);assert get(c).status_code==200
    d=json.loads((state/'policy.json').read_text());d['grants'][0]['enabled']=False
    p=state/'policy.new';p.write_text(json.dumps(d));p.replace(state/'policy.json')
    assert get(c).status_code==403

def test_limiter_bounded_and_does_not_trust_identity(state):
    limiter=BoundedLimiter(limit=1,capacity=2);c=client(state,limiter=limiter)
    assert get(c).status_code==200
    assert get(c).status_code==429
    assert get(client(state,'10.11.31.4',limiter=limiter)).status_code==200
    assert get(client(state,'10.11.31.5',limiter=limiter)).status_code==200
    assert len(limiter.rows)==2

def test_replace_authenticator_without_changing_release_or_api(state):
    class Alternate:
        def authenticate(self, header, grants, now):
            return grants[0]
    c=client(state,authenticator=Alternate())
    assert c.get('/bootstrap/v1/config?image_id=tower-v1').json()['manifest']['config_revision']=='r1'
