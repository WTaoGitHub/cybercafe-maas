"""Controller baseline audit. Reads selected fields only, never serializes credentials."""
import json,subprocess,sys,time,urllib.request
from pathlib import Path
sys.path.insert(0,'/root/wtaobj')
from gpustack_admin import api
out={'checked_unix':time.time(),'kind':'read-only-baseline','models':[],'routes':[]}
for x in api('/v2/models')['items']:
 out['models'].append({k:x.get(k) for k in ['id','name','backend','backend_version','replicas','ready_replicas','generic_proxy']})
for x in api('/v2/model-routes')['items']:
 out['routes'].append({k:x.get(k) for k in ['id','name','generic_proxy','access_policy','ready_targets']})
for name in ['wta-gpustack-server','wta-gpustack-worker']:
 x=json.loads(subprocess.check_output(['docker','inspect',name]))[0]
 out[name]={'image':x['Config']['Image'],'image_id':x['Image']}
try:
 with urllib.request.urlopen('http://127.0.0.1:18080/healthz',timeout=10) as r:out['health_status']=r.status
except Exception:out['health_status']='unavailable'
with urllib.request.urlopen('http://127.0.0.1:18080/version',timeout=10) as r:out['version']=json.load(r)
out['postgres_cached']=bool(subprocess.check_output(['docker','images','-q','postgres:16-alpine']).strip())
Path('/root/wtaobj/maas-baseline.json').write_text(json.dumps(out,indent=2)+'\n');print(json.dumps(out))
