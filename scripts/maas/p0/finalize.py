"""Read-only service/config recheck and cleanup verification after P0 probes."""
import base64,json,shutil,sys,time,urllib.request
from pathlib import Path
from common import admin,request
root=Path('/root/wtaobj/maas-p0');result={'checked_unix':time.time(),'temporary_resources_remaining':{}}
for kind,field in [('models','name'),('model-routes','name'),('inference-backends','backend_name'),('users','username'),('organizations','name')]:
 remaining=[];page=1
 while True:
  rows=admin('/v2/'+kind+'?perPage=100&page='+str(page))['items']
  remaining.extend(x['id'] for x in rows if str(x.get(field,'')).startswith('maas-p0-'))
  if len(rows)<100:break
  page+=1
 result['temporary_resources_remaining'][kind]=remaining
assert not any(result['temporary_resources_remaining'].values())
model=admin('/v2/models/1');route=admin('/v2/model-routes/1')
result['original_model']={k:model.get(k) for k in ['id','name','backend_version','replicas','ready_replicas','generic_proxy']}
result['original_route']={k:route.get(k) for k in ['id','name','generic_proxy','access_policy']}
assert model['name']=='rapidocr-gpu' and model['backend_version']=='0.3' and model['replicas']==model['ready_replicas']==2 and not route['generic_proxy']
key=Path('/root/wtaobj/.gpustack-api-key').read_text().strip()
image=Path('/root/wtaobj/service-validation/fixtures/upright.png').read_bytes()
status,raw,_=request('/v1/chat/completions',{'model':'rapidocr-gpu','stream':False,'messages':[{'role':'user','content':[{'type':'image_url','image_url':{'url':'data:image/png;base64,'+base64.b64encode(image).decode()}}]}]},key)
assert status==200
ocr=json.loads(json.loads(raw)['choices'][0]['message']['content']);assert ocr['lines']
result['original_ocr_smoke']={'status':status,'lines':len(ocr['lines'])}
result['private_probe_bundle_removed']=not (root/'.tenant-bundle.json').exists();assert result['private_probe_bundle_removed']
space=shutil.disk_usage('/root/wtaobj');result['storage']={'free_bytes':space.free,'total_bytes':space.total}
result['ok']=True
(root/'final-verification.json').write_text(json.dumps(result,indent=2)+'\n');print(json.dumps(result))
