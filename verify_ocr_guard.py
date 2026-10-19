"""Controller smoke checks of guarded deployment on every replica."""
import base64,io,json,urllib.request
from pathlib import Path
from PIL import Image
from gpustack_admin import api,base,opener
model=next(x for x in api('/v2/models')['items'] if x['name']=='rapidocr-gpu')
image=Image.open('/root/wtaobj/service-validation/fixtures/upright.png').convert('RGB')
def encoded(im):
 f=io.BytesIO();im.save(f,format='PNG');return base64.b64encode(f.getvalue()).decode()
records=[]
for row in api('/v2/models/'+str(model['id'])+'/instances')['items']:
 root='http://'+row['worker_ip']+':'+str(row['port'])
 for angle,im in [(0,image),(180,image.transpose(Image.Transpose.ROTATE_180))]:
  req=urllib.request.Request(root+'/ocr',data=json.dumps({'image_base64':encoded(im)}).encode(),headers={'Content-Type':'application/json'})
  with urllib.request.urlopen(req,timeout=90) as r:result=json.load(r)
  assert len(result['lines'])>=40 and result['text']
  records.append({'worker':row['worker_name'],'angle':angle,'lines':len(result['lines']),'node':result['node']})
report={'ok':True,'per_worker_upright_and_180':records,'backend_version':model['backend_version']}
Path('/root/wtaobj/ocr-guard-verification.json').write_text(json.dumps(report,indent=2)+'\n');print(json.dumps(report))
