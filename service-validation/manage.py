"""Controller-only management of isolated test deployment and short-lived test key."""
import hashlib,json,os,sys,time
from pathlib import Path
sys.path.insert(0,'/root/wtaobj')
from gpustack_admin import api
ROOT=Path('/root/wtaobj/service-validation');ROOT.mkdir(parents=True,exist_ok=True)
STATE=ROOT/'deployment.json';NAME='wta-ocr-validation';BACKEND='OCR-validation-custom'
def prepare():
    state={}
    old=api('/v2/models/1');state['original']={k:old.get(k) for k in ['id','name','replicas','backend','backend_version','worker_selector']}
    backend=next((x for x in api('/v2/inference-backends')['items'] if x['backend_name']==BACKEND),None)
    if backend is None:backend=api('/v2/inference-backends',{'backend_name':BACKEND,'health_check_path':'/health','default_version':'0.1','version_configs':{'0.1':{'image_name':'quay.io/cybercafe-ocr-validation:0.1','custom_framework':'cuda','entrypoint':'python3 /app/validation_server.py','run_command':'--port {{port}} --model {{model_name}}'}}})
    state['backend_id']=backend['id']
    model=next((x for x in api('/v2/models')['items'] if x['name']==NAME),None)
    if model is None:model=api('/v2/models',{'name':NAME,'description':'Temporary OCR direction, throughput and GPU-energy validation','source':'local_path','local_path':'/root/wtaobj/gpu-ocr','cluster_id':1,'replicas':0,'categories':['llm'],'placement_strategy':'spread','worker_selector':{'worker-name':'XZ-31-002'},'backend':BACKEND,'backend_version':'0.1','cpu_offloading':False,'distributed_inference_across_workers':False,'env':{'GPUSTACK_MODEL_VRAM_CLAIM':str(6*1024**3)}})
    state['model_id']=model['id']
    route=next((x for x in api('/v2/model-routes')['items'] if x['name']==NAME),None)
    if route is None:route=api('/v2/model-routes',{'name':NAME,'categories':['llm'],'targets':[{'model_id':model['id'],'weight':100}]})
    state['route_id']=route['id']
    if STATE.exists():state.update({k:v for k,v in json.loads(STATE.read_text()).items() if k=='key_id'})
    key_path=ROOT/'.benchmark-key'
    if not key_path.exists():
        result=api('/v2/api-keys',{'name':'wta-ocr-validation-temporary','description':'Temporary inference-only OCR validation key','allowed_model_names':[NAME],'scope':['inference'],'expires_in':86400})
        key=result.get('api_key') or result.get('key') or result.get('value')
        if not key and result.get('access_key') and result.get('secret_key'):key='gpustack_'+result['access_key']+'_'+result['secret_key']
        if not key:raise RuntimeError('Key response format unsupported; no credential printed')
        fd=os.open(key_path,os.O_WRONLY|os.O_CREAT|os.O_TRUNC,0o600)
        with os.fdopen(fd,'w') as f:f.write(key+'\n')
        state['key_id']=result['id']
    STATE.write_text(json.dumps(state,indent=2)+'\n');print('Test deployment prepared; temporary inference-only key saved privately.',flush=True)
def scale(n):
    state=json.loads(STATE.read_text());m=api('/v2/models/'+str(state['model_id']));m['replicas']=n;m['worker_selector']={'worker-name':'XZ-31-002'} if n==1 else {};api('/v2/models/'+str(m['id']),m,'PUT')
    for i in range(200):
        ins=api('/v2/models/'+str(m['id'])+'/instances')['items']
        if len(ins)==n and all(x['state']=='running' for x in ins) and (n<2 or len({x['worker_name'] for x in ins})==2):
            output={'replicas':n,'instances':[{k:x.get(k) for k in ['id','worker_name','state','worker_ip','port','backend_version']} for x in ins]};(ROOT/f'deployment-{n}-replicas.json').write_text(json.dumps(output,indent=2)+'\n');print(json.dumps(output),flush=True);return
        if i%10==0:print('Waiting',n,[{k:x.get(k) for k in ['worker_name','state','state_message']} for x in ins],flush=True)
        time.sleep(3)
    raise RuntimeError('Test replicas did not become ready')
def cleanup():
    state=json.loads(STATE.read_text())
    for path in ['/v2/model-routes/'+str(state['route_id']),'/v2/models/'+str(state['model_id']),'/v2/api-keys/'+str(state['key_id']),'/v2/inference-backends/'+str(state['backend_id'])]:
        api(path,method='DELETE')
    (ROOT/'.benchmark-key').unlink(missing_ok=True)
    current=api('/v2/models/1');current={k:current.get(k) for k in state['original']};assert current==state['original'],'Original deployment metadata changed'
    (ROOT/'cleanup.json').write_text(json.dumps({'temporary_resources_deleted':True,'temporary_key_revoked':True,'original_model_unchanged':True,'original':current},indent=2)+'\n');print('Temporary resources removed; key revoked; original deployment unchanged.')
if __name__=='__main__':
    if sys.argv[1]=='prepare':prepare()
    elif sys.argv[1]=='scale':scale(int(sys.argv[2]))
    elif sys.argv[1]=='cleanup':cleanup()
