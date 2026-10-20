"""Controller probe utilities. Private admin material is read in memory, never logged."""
import http.cookiejar,json,secrets,sys,time,urllib.error,urllib.parse,urllib.request
from pathlib import Path
sys.path.insert(0,'/root/wtaobj')
from gpustack_admin import api as admin
BASE='http://127.0.0.1:18080'

def request(path,body=None,key=None,opener=None,headers=None,method=None,base=BASE):
 h={'Content-Type':'application/json',**(headers or {})}
 if key:h['Authorization']='Bearer '+key
 raw=json.dumps(body).encode() if body is not None else None
 try:
  with (opener or urllib.request.build_opener()).open(urllib.request.Request(base+path,data=raw,headers=h,method=method),timeout=30) as r:
   return r.status,r.read(),{k.lower():v for k,v in r.headers.items()}
 except urllib.error.HTTPError as e:return e.code,e.read(),{k.lower():v for k,v in e.headers.items()}

def login(username,password):
 op=urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
 op.open(urllib.request.Request(BASE+'/auth/login',data=urllib.parse.urlencode({'username':username,'password':password}).encode()),timeout=15).close();return op

class Resources:
 def __init__(self):self.created=[];self.failures=[];self.prefix='maas-p0-'+secrets.token_hex(4)
 def create(self,path,body):
  result=admin(path,body);self.created.append((path,result['id']));return result
 def user(self,suffix):
  password='Ab9!'+secrets.token_hex(20)
  u=self.create('/v2/users',{'username':self.prefix+'-'+suffix,'password':password,'is_admin':False})
  return u,login(u['username'],password)
 def key(self,op,name,models,org=None,expires=600):
  headers={'X-Organization-Id':str(org)} if org else {}
  code,raw,_=request('/v2/api-keys',{'name':self.prefix+'-'+name,'scope':['inference'],'allowed_model_names':models,'expires_in':expires},opener=op,headers=headers)
  assert code in (200,201),('key_create',code)
  result=json.loads(raw);self.created.append(('/v2/api-keys',result['id']))
  return result,result.get('value') or result.get('api_key')
 def cleanup(self):
  # Keys/routes/models before organizations/users/backends regardless creation order.
  priorities={'/v2/api-keys':0,'/v2/model-routes':1,'/v2/models':2,'/v2/organizations':3,'/v2/users':4,'/v2/inference-backends':5}
  for path,ident in sorted(self.created,key=lambda r:priorities.get(r[0],9)):
   try:admin(path+'/'+str(ident),method='DELETE')
   except Exception:self.failures.append({'path':path,'id':ident})
  return not self.failures

def prepare_backend(res):
 b=res.create('/v2/inference-backends',{'backend_name':res.prefix+'-custom','health_check_path':'/health','default_version':'p0-r1','version_configs':{'p0-r1':{'image_name':'quay.io/cybercafe-maas-p0:0.1','custom_framework':'cuda','entrypoint':'python3 /app/probe_backend.py','run_command':'--port {{port}} --model {{model_name}}'}}})
 m=res.create('/v2/models',{'name':res.prefix,'source':'local_path','local_path':'/root/wtaobj/gpu-ocr','cluster_id':1,'replicas':2,'categories':['llm'],'generic_proxy':True,'placement_strategy':'spread','backend':b['backend_name'],'backend_version':'p0-r1','cpu_offloading':False,'distributed_inference_across_workers':False,'env':{'GPUSTACK_MODEL_VRAM_CLAIM':str(128*1024**2)}})
 routes=admin('/v2/model-routes')['items'];route=next((x for x in routes if x.get('created_model_id')==m['id'] or x['name']==m['name']),None)
 if route is None:route=res.create('/v2/model-routes',{'name':m['name'],'categories':['llm'],'generic_proxy':True,'targets':[{'model_id':m['id'],'weight':100}]})
 else:res.created.append(('/v2/model-routes',route['id']))
 for n in range(120):
  instances=admin('/v2/models/'+str(m['id'])+'/instances')['items']
  if len(instances)==2 and all(x['state']=='running' for x in instances):break
  if n%10==0:print('Waiting for isolated probe backend:',[x['state'] for x in instances],flush=True)
  time.sleep(2)
 else:raise RuntimeError('Probe backend not ready')
 return b,m,route,instances

def stats(instances,case):
 total=0
 for row in instances:
  url='http://'+row['worker_ip']+':'+str(row['port'])+'/probe/stats'
  with urllib.request.urlopen(url,timeout=10) as response:total+=json.load(response)['counts'].get(case,0)
 return total
