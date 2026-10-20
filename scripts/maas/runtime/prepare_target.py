"""Create a separately versioned, retained P2 pilot target. Never overwrite its pin."""
import json,os,sys,time
from pathlib import Path
sys.path.insert(0,'/root/wtaobj/maas-framework/scripts/maas/p0')
from common import Resources,admin
root=Path('/root/wtaobj/maas-runtime');root.mkdir(mode=0o700,exist_ok=True)
path=root/'target.json'
if path.exists():
    ids=json.loads(path.read_text());model=admin('/v2/models/'+str(ids['model']))
    assert model['name']=='maas-ocr-p2-r2'
    print('Existing retained pilot target reused');raise SystemExit(0)
res=Resources();res.prefix='maas-ocr-p2-r2'
try:
    backend=res.create('/v2/inference-backends',{'backend_name':'MaaS-OCR-P2-R2-custom','health_check_path':'/health','default_version':'p2-r2',
        'version_configs':{'p2-r2':{'image_name':'quay.io/cybercafe-maas-ocr:p2-r1','custom_framework':'cuda','entrypoint':'python3 /app/server.py','run_command':'--port {{port}} --model {{model_name}}'}}})
    model=res.create('/v2/models',{'name':res.prefix,'source':'local_path','local_path':'/root/wtaobj/gpu-ocr','cluster_id':1,'replicas':2,
        'categories':['llm'],'placement_strategy':'spread','backend':backend['backend_name'],'backend_version':'p2-r2',
        'cpu_offloading':False,'distributed_inference_across_workers':False,'env':{'GPUSTACK_MODEL_VRAM_CLAIM':str(2*1024**3)}})
    route=next((x for x in admin('/v2/model-routes')['items'] if x['name']==res.prefix),None)
    if route is None:
        route=res.create('/v2/model-routes',{'name':res.prefix,'categories':['llm'],'access_policy':'authed','targets':[{'model_id':model['id'],'weight':100}]})
    else:res.created.append(('/v2/model-routes',route['id']))
    for i in range(100):
        rows=admin('/v2/models/'+str(model['id'])+'/instances')['items']
        if len(rows)==2 and all(x['state']=='running' for x in rows):break
        if i%10==0:print('Pilot deployment:',[x['state'] for x in rows],flush=True)
        time.sleep(2)
    else:raise RuntimeError('Pilot target not ready')
    ids={'model':model['id'],'route':route['id'],'backend':backend['id'],'name':res.prefix,'resources':res.created}
    fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
    with os.fdopen(fd,'w') as f:json.dump(ids,f)
    print('Retained pilot target ready')
except BaseException:
    print('Failed pilot setup cleaned:',res.cleanup());raise
