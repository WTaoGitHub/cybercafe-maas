"""Controller integration: real GPUStack auth + isolated mock task API/worker/DB.
Creates a disposable non-admin user/key and removes them and smoke containers on exit.
No source databases or GPUStack model configuration are changed.
"""
import base64,http.cookiejar,json,os,secrets,socket,subprocess,sys,time,urllib.request,urllib.error,urllib.parse
from pathlib import Path
sys.path.insert(0,'/root/wtaobj')
from gpustack_admin import api
root=Path('/root/wtaobj/maas-framework');private=root/'.secrets'/'smoke';private.mkdir(mode=0o700,exist_ok=True)
user_id=None;key_id=None;report={'stage':'P1','real_gpustack_auth':True,'mock_inference_only':True,'checks':{}}
containers=['wta-maas-p1-smoke-api','wta-maas-p1-smoke-worker']
for name in containers:
 if subprocess.run(['docker','inspect',name],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL).returncode==0:raise RuntimeError('Smoke container name already in use')
sock=socket.socket();sock.bind(('127.0.0.1',18830));sock.close()
def private_write(name,data):
 p=private/name;fd=os.open(p,os.O_CREAT|os.O_TRUNC|os.O_WRONLY,0o600)
 with os.fdopen(fd,'w') as f:f.write(data)
 return p

def request(path,body=None,key=None,headers=None,method=None):
 if isinstance(body,dict):body=json.dumps(body).encode()
 h={'Authorization':'Bearer '+key} if key else {}
 if body is not None:h['Content-Type']='application/json'
 req=urllib.request.Request('http://127.0.0.1:18830'+path,data=body,headers={**h,**(headers or {})},method=method)
 try:
  with urllib.request.urlopen(req,timeout=15) as r:return r.status,json.load(r)
 except urllib.error.HTTPError as e:return e.code,json.load(e)

def start(name,command):
 subprocess.run(['docker','run','-d','--name',name,'--network','host','--env-file',str(private/'app.env'),'-v',str(root)+':/work','-w','/work','python:3.11-slim',*command],check=True,stdout=subprocess.DEVNULL)

def wait_task(ident,key,timeout=50):
 end=time.monotonic()+timeout
 while time.monotonic()<end:
  code,row=request('/maas/v1/tasks/'+ident,key=key)
  if row.get('state') in ('SUCCEEDED','FAILED','CANCELLED'):return row
  time.sleep(.3)
 raise RuntimeError('Task did not reach terminal state')
try:
 # A separate DB from the pytest schema, still entirely independent from GPUStack.
 exists=subprocess.check_output(['docker','exec','wta-maas-p1-test-db','psql','-U','maas','-d','postgres','-tAc',"SELECT 1 FROM pg_database WHERE datname='maas_smoke_test'"]).strip()
 if not exists:subprocess.run(['docker','exec','wta-maas-p1-test-db','psql','-U','maas','-d','postgres','-c','CREATE DATABASE maas_smoke_test'],check=True,stdout=subprocess.DEVNULL)
 user=api('/v2/users',{'username':'maas-smoke-'+secrets.token_hex(4),'password':'Ab9!'+secrets.token_hex(20),'is_admin':False});user_id=user['id']
 # Use a one-time password update for login without storing it.
 password='Ab9!'+secrets.token_hex(20)
 api('/v2/users/'+str(user_id),{'username':user['username'],'password':password,'is_admin':False},'PUT')
 opener=urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
 opener.open(urllib.request.Request('http://127.0.0.1:18080/auth/login',data=urllib.parse.urlencode({'username':user['username'],'password':password}).encode()),timeout=15).close()
 with opener.open(urllib.request.Request('http://127.0.0.1:18080/v2/api-keys',data=json.dumps({'name':'maas-smoke','scope':['inference'],'allowed_model_names':['rapidocr-gpu'],'expires_in':600}).encode(),headers={'Content-Type':'application/json'})) as r:key=json.load(r)
 key_id=key['id'];credential=key.get('value') or key.get('api_key');assert credential
 private_write('pilot-key',credential)
 private_write('pepper',secrets.token_hex(32))
 private_write('bindings.json',json.dumps({'credentials':[{'id':'smoke','project':'smoke','secret_file':'/work/.secrets/smoke/pilot-key','models':['mock.echo']}]}))
 url=(root/'.secrets/test.env').read_text().strip().split('=',1)[1].replace('/maas_test','/maas_smoke_test')
 private_write('app.env','\n'.join(['MAAS_DATABASE_URL='+url,'MAAS_ARTIFACT_ROOT=/work/smoke-artifacts','MAAS_GPUSTACK_URL=http://127.0.0.1:18080','MAAS_BINDINGS_FILE=/work/.secrets/smoke/bindings.json','MAAS_CATALOG_FILE=/work/deploy/maas/catalog.example.json','MAAS_PEPPER_FILE=/work/.secrets/smoke/pepper'])+'\n')
 start(containers[0],['/work/.venv/bin/uvicorn','maas_platform.api:create_app','--factory','--host','127.0.0.1','--port','18830'])
 for _ in range(60):
  try:
   if request('/healthz')[0]==200:break
  except Exception:time.sleep(.2)
 else:raise RuntimeError('API not ready')
 code,art=request('/maas/v1/artifacts',b'hello from live smoke',credential,{'Content-Type':'text/plain'});assert code==201
 payload={'model':'mock.echo','input':{'text':'live upstream authentication','artifact_id':art['id']},'parameters':{'delay_seconds':3}}
 code,row=request('/maas/v1/tasks',payload,credential,{'Idempotency-Key':'smoke-'+secrets.token_hex(8)});assert code==202
 ident=row['id'];report['checks']['accepted_and_persisted']=True
 # Restart the API before executing anything: queued state must survive.
 subprocess.run(['docker','restart',containers[0]],check=True,stdout=subprocess.DEVNULL)
 for _ in range(60):
  try:
   code,row=request('/maas/v1/tasks/'+ident,key=credential)
   if code==200:break
  except Exception:pass
  time.sleep(.2)
 assert row['state']=='QUEUED';report['checks']['api_restart_keeps_accepted_task']=True
 start(containers[1],['/work/.venv/bin/python','-m','maas_platform.worker'])
 for _ in range(60):
  code,row=request('/maas/v1/tasks/'+ident,key=credential)
  if row['state']=='RUNNING':break
  time.sleep(.05)
 assert row['state']=='RUNNING'
 subprocess.run(['docker','kill',containers[1]],check=True,stdout=subprocess.DEVNULL)
 subprocess.run(['docker','rm',containers[1]],check=True,stdout=subprocess.DEVNULL)
 start(containers[1],['/work/.venv/bin/python','-m','maas_platform.worker'])
 row=wait_task(ident,credential);assert row['state']=='SUCCEEDED' and row['attempts']==2
 report['checks']['worker_sigkill_lease_recovery']=True;report['attempts']=row['attempts']
 code,result=request('/maas/v1/artifacts/'+row['result_artifact_id'],key=credential)
 assert code==200 and result['outputs']['text']=='live upstream authentication'
 report['checks']['durable_result_download']=True
 # Schema only; no production model changes or GPU load.
 code,openapi=request('/openapi.json');assert code==200
 (root/'openapi.json').write_text(json.dumps(openapi,indent=2)+'\n')
 report['checks']['openapi_export']=True
finally:
 for name in containers:
  subprocess.run(['docker','rm','-f',name],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
 if key_id is not None:api('/v2/api-keys/'+str(key_id),method='DELETE')
 if user_id is not None:api('/v2/users/'+str(user_id),method='DELETE')
 for path in private.glob('*'):path.unlink()
 report['temporary_credentials_and_smoke_containers_removed']=True
 (root/'smoke-live.json').write_text(json.dumps(report,indent=2)+'\n')
print(json.dumps(report))
