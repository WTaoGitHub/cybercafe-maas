"""Child process: real upstream credentials + two project object isolation on PostgreSQL."""
import json,os,sys,tempfile
from pathlib import Path
sys.path.insert(0,'/root/wtaobj/maas-framework')
from fastapi.testclient import TestClient
from maas_platform.api import create_app
from maas_platform.config import Settings
bundle=json.loads(Path(sys.argv[1]).read_text());root=Path('/root/wtaobj/maas-p0');out={}
with tempfile.TemporaryDirectory(prefix='tenant-',dir=root) as temp:
 p=Path(temp)
 def secret(name,text):
  f=p/name;f.write_text(text);f.chmod(0o600);return f
 records=[]
 for item in bundle:
  keyfile=secret(item['id'],item['key'])
  records.append({'id':item['id'],'project':'org:'+str(item['org']),'secret_file':str(keyfile),'models':['mock.echo']})
 bindings=secret('bindings.json',json.dumps({'credentials':records}));pepper=secret('pepper',os.urandom(32).hex())
 catalog=p/'catalog.json';catalog.write_text(json.dumps({'mock.echo':{'driver':'mock','revision':'1','max_runtime_s':30}}))
 url=Path('/root/wtaobj/maas-framework/.secrets/test.env').read_text().strip().split('=',1)[1].replace('/maas_test','/maas_p0_test')
 settings=Settings(url,p/'artifacts','http://127.0.0.1:18080',bindings,catalog,pepper)
 with TestClient(create_app(settings),raise_server_exceptions=False) as client:
  ha={'Authorization':'Bearer '+bundle[0]['key']};hb={'Authorization':'Bearer '+bundle[1]['key']}
  art=client.post('/maas/v1/artifacts',headers=ha,content=b'isolated-input');assert art.status_code==201
  response=client.post('/maas/v1/tasks',headers={**ha,'Idempotency-Key':os.urandom(8).hex()},json={'model':'mock.echo','input':{'artifact_id':art.json()['id']}});assert response.status_code==202
  ident=response.json()['id']
  out['a_reads_own_task']=client.get('/maas/v1/tasks/'+ident,headers=ha).status_code
  out['b_cannot_read_a_task']=client.get('/maas/v1/tasks/'+ident,headers=hb).status_code
  out['b_cannot_download_a_artifact']=client.get('/maas/v1/artifacts/'+art.json()['id'],headers=hb).status_code
  out['forged_org_header_cannot_change_project']=client.get('/maas/v1/tasks/'+ident,headers={**hb,'X-Organization-Id':str(bundle[0]['org']),'X-Project':'org:'+str(bundle[0]['org'])}).status_code
  out['b_cannot_cancel_a_task']=client.post('/maas/v1/tasks/'+ident+'/cancel',headers=hb).status_code
  assert out['a_reads_own_task']==200 and all(code==404 for key,code in out.items() if key!='a_reads_own_task')
  client.post('/maas/v1/tasks/'+ident+'/cancel',headers=ha)
print(json.dumps(out))
