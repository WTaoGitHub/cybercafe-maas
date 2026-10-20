"""Add a dedicated pilot deployment without changing existing OCR model/backend/tag."""
import json,sys,time
from pathlib import Path
sys.path.insert(0,'/root/wtaobj/maas-framework/scripts/maas/p0')
from common import Resources,admin
res=Resources();res.prefix='maas-ocr-p2-r1'
root=Path('/root/wtaobj/maas-p2');root.mkdir(exist_ok=True)
if (root/'target.json').exists():raise RuntimeError('Target already registered; inspect before changing it')
try:
    backend=res.create('/v2/inference-backends',{'backend_name':'MaaS-OCR-P2-custom','health_check_path':'/health',
        'default_version':'p2-r1','version_configs':{'p2-r1':{'image_name':'quay.io/cybercafe-maas-ocr:p2-r1',
        'custom_framework':'cuda','entrypoint':'python3 /app/server.py','run_command':'--port {{port}} --model {{model_name}}'}}})
    model=res.create('/v2/models',{'name':res.prefix,'source':'local_path','local_path':'/root/wtaobj/gpu-ocr',
        'cluster_id':1,'replicas':2,'categories':['llm'],'placement_strategy':'spread','backend':backend['backend_name'],
        'backend_version':'p2-r1','cpu_offloading':False,'distributed_inference_across_workers':False,
        'env':{'GPUSTACK_MODEL_VRAM_CLAIM':str(2*1024**3)}})
    route=next((x for x in admin('/v2/model-routes')['items'] if x['name']==res.prefix),None)
    if route is None:
        route=res.create('/v2/model-routes',{'name':res.prefix,'categories':['llm'],'access_policy':'authed','targets':[{'model_id':model['id'],'weight':100}]})
    else:res.created.append(('/v2/model-routes',route['id']))
    for i in range(120):
        instances=admin('/v2/models/'+str(model['id'])+'/instances')['items']
        if len(instances)==2 and all(x['state']=='running' for x in instances):break
        if i%10==0:print('Dedicated OCR state:',[x['state'] for x in instances],flush=True)
        time.sleep(2)
    else:raise RuntimeError('Dedicated OCR did not become ready')
    assert {x['worker_name'] for x in instances}=={'XZ-31-001','XZ-31-002'}
    result={'backend':backend['id'],'model':model['id'],'route':route['id'],'name':res.prefix,
            'resources':res.created,'image':'quay.io/cybercafe-maas-ocr:p2-r1'}
    (root/'target.json').write_text(json.dumps(result,indent=2)+'\n')
    print('Dedicated pinned OCR target ready:',model['id'],route['id'])
except BaseException:
    print('Deployment failed; temporary resources removed:',res.cleanup());raise
