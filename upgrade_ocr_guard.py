"""Run on controller after preloading 0.3 on both workers; --rollback restores snapshot."""
import json,sys,time,urllib.request
from pathlib import Path
from gpustack_admin import api
ROOT=Path('/root/wtaobj');snapshot=ROOT/'ocr-guard-rollback.json'
backend=next(x for x in api('/v2/inference-backends')['items'] if x['backend_name']=='RapidOCR-custom')
model=next(x for x in api('/v2/models')['items'] if x['name']=='rapidocr-gpu')
rollback='--rollback' in sys.argv
if rollback:
 old=json.loads(snapshot.read_text());target=old['model_version'];backend['default_version']=old['backend_default_version']
else:
 if not snapshot.exists():snapshot.write_text(json.dumps({'model_version':model['backend_version'],'backend_default_version':backend['default_version']},indent=2)+'\n')
 target='0.3';version=dict(backend['version_configs'][model['backend_version']]);version['image_name']='quay.io/cybercafe-gpu-ocr:0.3';backend['version_configs'][target]=version;backend['default_version']=target
api('/v2/inference-backends/'+str(backend['id']),backend,'PUT')
model['backend_version']=target;api('/v2/models/'+str(model['id']),model,'PUT')
path='/v2/models/'+str(model['id'])+'/instances'
for old in api(path)['items']:
 if old['backend_version']==target:continue
 current=api(path)['items'];assert any(x['state']=='running' and x['id']!=old['id'] for x in current),'No other healthy replica; refusing replacement'
 api('/v2/model-instances/'+str(old['id']),method='DELETE');print('Replacing',old['worker_name'],flush=True)
 for _ in range(180):
  instances=api(path)['items']
  if len(instances)==model['replicas'] and all(x['state']=='running' for x in instances):break
  time.sleep(1)
 else:raise RuntimeError('Replacement timeout; run --rollback')
instances=api(path)['items'];assert len(instances)==model['replicas'] and all(x['state']=='running' and x['backend_version']==target for x in instances)
health=[]
for row in instances:
 with urllib.request.urlopen('http://'+row['worker_ip']+':'+str(row['port'])+'/health',timeout=15) as response:status=json.load(response)
 if not rollback:assert status['direction_policy']=='guarded' and status['version']=='0.3'
 health.append({'worker':row['worker_name'],'instance_id':row['id'],'health':status})
report={'backend_version':target,'replicas':len(instances),'health':health,'rollback_command':'python3 /root/wtaobj/upgrade_ocr_guard.py --rollback'}
(ROOT/'ocr-guard-deployment.json').write_text(json.dumps(report,indent=2)+'\n');print(json.dumps(report),flush=True)
