"""Trusted controller-side collector; reads admin key outside API/executor containers."""
import json,os,subprocess,sys,time,urllib.request
from pathlib import Path
sys.path.insert(0,'/root/wtaobj')
sys.path.insert(0,'/root/wtaobj/maas-framework')
from gpustack_admin import api
from maas_platform.versioning import register_pin,verify_pin,VersionPin
root=Path('/root/wtaobj/maas-p2')
ids=json.loads((root/'target.json').read_text())
inventory=json.load(sys.stdin)
route=api('/v2/model-routes/'+str(ids['route']))
model=api('/v2/models/'+str(ids['model']))
backend=api('/v2/inference-backends/'+str(ids['backend']))
targets=api('/v2/model-route-targets?route_id='+str(ids['route']))['items']
with urllib.request.urlopen('http://127.0.0.1:18080/version',timeout=10) as r:version=json.load(r)
policy_script=Path('/root/wtaobj/maas-framework/scripts/maas/p0/inspect_gateway.py').read_text()
policies=json.loads(subprocess.check_output(['docker','exec','-i','wta-gpustack-server','python','-c',policy_script],text=True))
route_ingress=next(x for x in policies['ingresses'] if x['name']=='ai-route-route-'+str(ids['route'])+'.internal')
ann=route_ingress['policies']
# v2.2.3 real probe: configured 2 means two additional sends, up to 3 total.
retries=int(ann.get('higress.io/proxy-next-upstream-tries','0'))
assert retries==2 and ann.get('higress.io/proxy-next-upstream')=='error,timeout,http_503,http_502,non_idempotent', 'Gateway policy requires review'
max_attempts=3
assert {x['name'] for x in api('/v2/workers')['items']}==set(inventory), 'Eligible worker set requires explicit re-registration'
instances=api('/v2/models/'+str(ids['model'])+'/instances')['items']
assert len(instances)==2 and all(x['state']=='running' for x in instances)
assert {x['worker_name'] for x in instances}==set(inventory)
image_ids={x['image_id'] for x in inventory.values()};assert len(image_ids)==1
for row in inventory.values():
    assert row['running_image_ids']==[row['image_id']], 'Running container must use the pinned content'
    assert row['paused'] is False
pin_path=root/'pin.json'
if not pin_path.exists():
    pin=register_pin('ocr-p2-r1',route,targets,model,backend,next(iter(image_ids)),list(inventory))
else:pin=VersionPin(**json.loads(pin_path.read_text()))
verify_pin(pin,version,route,targets,model,backend,inventory)
snapshot={'observed_at':time.time(),'version':version,'route':route,'targets':targets,
          'model':model,'backend':backend,'inventory':inventory,'gateway_max_attempts':max_attempts,
          'gateway_policy':ann,'instances':[{k:x[k] for k in ('id','worker_name','worker_ip','port','state')} for x in instances]}
def write(path,value):
    temp=path.with_suffix('.tmp')
    fd=os.open(temp,os.O_CREAT|os.O_TRUNC|os.O_WRONLY,0o600)
    with os.fdopen(fd,'w') as f:json.dump(value,f);f.flush();os.fsync(f.fileno())
    os.replace(temp,path)
if not pin_path.exists():write(pin_path,pin.public())
write(root/'preflight.json',snapshot)
print('Trusted control, image and gateway preflight refreshed')
