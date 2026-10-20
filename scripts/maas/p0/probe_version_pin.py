"""Validate immutable registration against disposable real GPUStack control records."""
import copy,json,sys
from pathlib import Path
sys.path.insert(0,'/root/wtaobj/maas-framework')
from maas_platform.versioning import register_pin,verify_pin
from maas_platform.errors import ServiceError
from common import Resources,admin,request
root=Path('/root/wtaobj/maas-p0');res=Resources();out={'checks':{},'production_models_mutated':False}
try:
 inventory=json.loads((root/'image-inventory.json').read_text());ids={r['image_id'] for r in inventory.values()};assert len(ids)==1
 backend=res.create('/v2/inference-backends',{'backend_name':res.prefix+'-custom','health_check_path':'/health','default_version':'p0-r1','version_configs':{'p0-r1':{'image_name':'quay.io/cybercafe-maas-p0:0.1','custom_framework':'cuda','entrypoint':'python3 /app/probe_backend.py','run_command':'--port {{port}} --model {{model_name}}'}}})
 model=res.create('/v2/models',{'name':res.prefix,'source':'local_path','local_path':'/root/wtaobj/gpu-ocr','cluster_id':1,'replicas':0,'categories':['llm'],'backend':backend['backend_name'],'backend_version':'p0-r1','cpu_offloading':False,'distributed_inference_across_workers':False})
 route=res.create('/v2/model-routes',{'name':res.prefix,'categories':['llm'],'access_policy':'authed','targets':[{'model_id':model['id'],'weight':100}]})
 def snapshot():
  return (admin('/v2/model-routes/'+str(route['id'])),admin('/v2/model-route-targets?route_id='+str(route['id']))['items'],admin('/v2/models/'+str(model['id'])),admin('/v2/inference-backends/'+str(backend['id'])))
 code,raw,_=request('/version');version=json.loads(raw)
 r,t,m,b=snapshot();pin=register_pin('p0-r1',r,t,m,b,next(iter(ids)),list(inventory))
 def check():return verify_pin(pin,version,*snapshot(),inventory)
 out['pin']=pin.public();out['checks']['unchanged_real_control_records']=check()
 changed=admin('/v2/models/'+str(model['id']));changed['env']={'P0_DRIFT':'changed'};admin('/v2/models/'+str(model['id']),changed,'PUT')
 try:check();raise AssertionError('Deployment drift accepted')
 except ServiceError as e:assert e.code=='MODEL_REVISION_CHANGED';out['checks']['actual_deployment_change_rejected']={'code':e.code}
 # The original immutable snapshot plus a reused image tag must fail as well.
 changed_inventory=copy.deepcopy(inventory);changed_inventory[next(iter(changed_inventory))]['image_id']='sha256:'+'0'*64
 try:verify_pin(pin,version,r,t,m,b,changed_inventory);raise AssertionError('Image drift accepted')
 except ServiceError as e:assert e.code=='MODEL_IMAGE_CHANGED';out['checks']['tag_reuse_rejected']={'code':e.code}
 out['ok']=True
finally:
 out['cleanup_ok']=res.cleanup();out['cleanup_failures']=res.failures
 (root/'version-acceptance.json').write_text(json.dumps(out,indent=2)+'\n')
print(json.dumps(out))
