import json
from pathlib import Path
import httpx,pytest
from maas_platform.auth import AuthConnector
from maas_platform.config import Settings
from maas_platform.errors import ServiceError


def configured(tmp_path,rows=None):
 def secret(name,value):
  p=tmp_path/name;p.write_text(value);p.chmod(0o600);return p
 key=secret('key','registered-inference-only-key')
 row={'id':'key-a','project':'org-a','models':['ocr'],'secret_file':str(key)}
 registry=secret('registry',json.dumps({'credentials':rows or [row]}))
 cfg=Settings('unused',tmp_path/'artifacts','http://gpustack.invalid',registry,tmp_path/'catalog',secret('pepper','test-pepper'))
 return cfg,row,key


def test_bridge_sends_only_own_auth_headers(tmp_path):
 cfg,_,key=configured(tmp_path);seen=[]
 def upstream(request):
  seen.append(dict(request.headers));return httpx.Response(200,json={'data':[]})
 auth=AuthConnector(cfg,httpx.Client(transport=httpx.MockTransport(upstream)))
 identity=auth.authenticate(key.read_text());auth.authorize(identity,'ocr',{'driver':'gpustack','upstream_model':'ocr-r03'})
 assert len(seen)==2 and seen[1]['x-higress-llm-model']=='ocr-r03'
 for request in seen:
  assert 'x-gpustack-auth-cache' not in request and 'x-organization-id' not in request and 'cookie' not in request


def test_duplicate_key_cannot_bind_two_projects(tmp_path):
 cfg,row,key=configured(tmp_path)
 cfg.bindings_file.write_text(json.dumps({'credentials':[row,{**row,'id':'other','project':'org-b'}]}))
 auth=AuthConnector(cfg,httpx.Client(transport=httpx.MockTransport(lambda _:httpx.Response(200))))
 with pytest.raises(ServiceError,match='ambiguous') as error:auth.authenticate(key.read_text())
 assert error.value.code=='BINDING_INVALID'


def test_duplicate_id_fails_closed(tmp_path):
 cfg,row,key=configured(tmp_path)
 second=tmp_path/'second';second.write_text('a-different-key');second.chmod(0o600)
 cfg.bindings_file.write_text(json.dumps({'credentials':[row,{**row,'secret_file':str(second)}]}))
 auth=AuthConnector(cfg)
 with pytest.raises(ServiceError) as error:auth.bindings()
 assert error.value.status==503


def test_model_restriction_is_checked_after_valid_identity(tmp_path):
 cfg,_,key=configured(tmp_path)
 def upstream(request):return httpx.Response(403 if request.url.path=='/token-auth' else 200,json={'data':[]})
 auth=AuthConnector(cfg,httpx.Client(transport=httpx.MockTransport(upstream)))
 identity=auth.authenticate(key.read_text())
 with pytest.raises(ServiceError) as error:auth.authorize(identity,'ocr',{'driver':'gpustack','upstream_model':'denied-model'})
 assert error.value.code=='AUTH_DENIED'


def test_registry_error_and_world_readable_secret_fail_closed(tmp_path):
 cfg,_,key=configured(tmp_path);auth=AuthConnector(cfg)
 key.chmod(0o644)
 with pytest.raises(ServiceError) as error:auth.bindings()
 assert error.value.code=='BINDING_INVALID'
