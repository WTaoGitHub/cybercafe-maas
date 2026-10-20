"""Disposable non-admin identity probe. No credentials are printed or persisted."""
import http.cookiejar,json,secrets,sys,time,urllib.request,urllib.error
from pathlib import Path
sys.path.insert(0,'/root/wtaobj')
from gpustack_admin import api
BASE='http://127.0.0.1:18080';user_id=None;key_id=None;result={'checks':{},'original_service_mutated':False}; extra_keys=[]
def call(path,body=None,headers=None,opener=None):
 req=urllib.request.Request(BASE+path,data=json.dumps(body).encode() if body is not None else None,headers={'Content-Type':'application/json',**(headers or {})})
 try:
  with (opener or urllib.request.build_opener()).open(req,timeout=20) as r:return r.status,r.read(),dict(r.headers)
 except urllib.error.HTTPError as e:return e.code,b'',{}
try:
 username='maas-probe-'+secrets.token_hex(4);password='Ab9!'+secrets.token_hex(20)
 user=api('/v2/users',{'username':username,'password':password,'is_admin':False});user_id=user['id']
 opener=urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
 req=urllib.request.Request(BASE+'/auth/login',data=__import__('urllib.parse',fromlist=['urlencode']).urlencode({'username':username,'password':password}).encode())
 opener.open(req,timeout=20).close()
 status,raw,_=call('/v2/api-keys',{'name':'maas-disposable-inference','scope':['inference'],'allowed_model_names':['rapidocr-gpu'],'expires_in':600},opener=opener)
 assert status in (200,201),('key_create',status)
 key=json.loads(raw);key_id=key['id'];secret=key.get('value') or key.get('api_key');assert secret
 headers={'Authorization':'Bearer '+secret}
 for label,path,extra in [('valid','/v1/models',{}),('model_allowed','/token-auth',{'x-higress-llm-model':'rapidocr-gpu'}),('model_denied','/token-auth',{'x-higress-llm-model':'maas-not-allowed'}),('management_denied','/v2/models',{}),('forged_cache','/token-auth',{'x-higress-llm-model':'rapidocr-gpu','x-gpustack-auth-cache':'forged'})]:
  code,_,reply=call(path,headers={**headers,**extra});result['checks'][label]={'status':code,'consumer_present':bool({k.lower():v for k,v in reply.items()}.get('x-mse-consumer'))}
 status,raw,_=call('/v2/api-keys',{'name':'maas-disposable-restricted','scope':['inference'],'allowed_model_names':['no-such-model'],'expires_in':600},opener=opener)
 assert status in (200,201)
 restricted=json.loads(raw);extra_keys.append(restricted['id'])
 restricted_key=restricted.get('value') or restricted.get('api_key')
 code,_,_=call('/token-auth',headers={'Authorization':'Bearer '+restricted_key,'x-higress-llm-model':'rapidocr-gpu'})
 result['checks']['existing_model_whitelist_denied']={'status':code}
 code,_,_=call('/token-auth',headers={'Authorization':'Bearer invalid','x-higress-llm-model':'rapidocr-gpu','x-gpustack-auth-cache':'forged'})
 result['checks']['invalid_with_forged_cache']={'status':code}
 code,_,_=call('/v1/models',headers={'Authorization':'Bearer invalid'});result['checks']['invalid']={'status':code}
 api('/v2/api-keys/'+str(key_id),method='DELETE');key_id=None
 code,_,_=call('/v1/models',headers=headers);result['checks']['immediate_after_key_delete']={'status':code}
 result['non_admin']=not user['is_admin']
finally:
 for ident in extra_keys:api('/v2/api-keys/'+str(ident),method='DELETE')
 if key_id is not None:api('/v2/api-keys/'+str(key_id),method='DELETE')
 if user_id is not None:api('/v2/users/'+str(user_id),method='DELETE')
 result['temporary_identity_deleted']=True
 Path('/root/wtaobj/maas-auth-probe.json').write_text(json.dumps(result,indent=2)+'\n')
print(json.dumps(result))
