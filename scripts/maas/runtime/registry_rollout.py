"""Scoped worker6 registry rollout. No old generic maintenance binding or VM action."""
import argparse,base64,contextlib,hashlib,io,json,os,socket,sys,time
from pathlib import Path
ROOT=Path('/root/wtaobj/maas-registry-rollout')
LIVE=Path('/root/wtaobj/maas-live')
REG=Path('/root/wtaobj/maas-registry')
sys.path[:0]=['/root/wtaobj','/root/wtaobj/maas-framework','/root/wtaobj/maas-framework/scripts/maas/runtime']
from gpustack_admin import api as raw_api
from e5_ocr_pilot import cli
STATE=ROOT/'window.private.json'
WORKER='e5-ba71e774d9ca94c0138c01fa'

def api(*args):
 with contextlib.redirect_stdout(io.StringIO()),contextlib.redirect_stderr(io.StringIO()):
  try:return raw_api(*args)
  except Exception:raise RuntimeError('GPUStack operation failed; inspect private state before retry') from None

def save(p,v):
 with p.open('w') as f:os.fchmod(f.fileno(),0o600);json.dump(v,f,indent=2);f.flush();os.fsync(f.fileno())

def native_maintenance(value):
 w=api('/v2/workers/6');assert w['name']==WORKER
 api('/v2/workers/6',{'name':w['name'],'labels':w['labels'],'maintenance':value},'PUT')

def prepare():
 ROOT.mkdir(mode=0o700,exist_ok=True);assert not STATE.exists()
 w=api('/v2/workers/6');m=api('/v2/models/17');s=cli('status');d=cli('drain-status')
 assert w['state']=='ready' and w['name']==WORKER and w['cluster_id']==3 and m['name']=='maas-ocr-e5-r3' and m['ready_replicas']==1
 assert not d['paused'] and not any(d[k] for k in ['active_tasks','backend_activity','sync_activity'])
 assert not s['tasks'].get('QUEUED',0) and not s['unknown_backend_activity'] and not s['unknown_sync_activity']
 private=Path('/root/wtaobj/maas-bootstrap/private');active=json.loads((private/'active.json').read_text())
 v={'phase':'BEFORE_PAUSE','worker':w,'model':m,'catalog':json.loads((LIVE/'catalog.json').read_text()),'old_active':active}
 save(STATE,v)
 paused=cli('pause','--reason','E5 registry-access-v1 worker migration');v.update(phase='PAUSED',generation=paused['generation']);save(STATE,v)
 assert cli('drain-status')['managed_requests_drained']
 native_maintenance({'enabled':True,'message':'maas:registry-access-v1:20261027'})
 m['replicas']=0;api('/v2/models/17',m,'PUT');v['phase']='SCALING_ZERO';save(STATE,v)
 end=time.monotonic()+90
 while api('/v2/models/17/instances')['items']:
  if time.monotonic()>end:raise RuntimeError('Stop not confirmed; keep paused')
  time.sleep(1)
 v['phase']='DRAINED';save(STATE,v)
 print(json.dumps({'phase':v['phase'],'generation':v['generation']}))

def publish():
 s=json.loads(STATE.read_text());assert s['phase']=='DRAINED'
 sys.path.insert(0,str(ROOT/'source'))
 from maas_bootstrap.client import LocalSettings,canonical
 from maas_bootstrap.publisher import Publisher
 private=Path('/root/wtaobj/maas-bootstrap/private');operator=Path('/root/wtaobj/maas-bootstrap/operator')
 old=json.loads((private/'releases'/(s['old_active']['revision']+'.json')).read_text())
 creds=json.loads((REG/'credentials.json').read_text())['reader']
 payload={**creds,'certificate_pem':(REG/'server.crt').read_text()}
 manifest={**old['manifest'],'config_revision':'e5-registry-r1','compatible_image_ids':['tower-e5-registry-v1'],
           'registry_access':{'schema_version':1,'endpoint':'10.11.31.1:18443','credential_id':'maas-reader-r1','payload_sha256':hashlib.sha256(canonical(payload)).hexdigest()}}
 registration={**old['registration'],'registry_access':payload}
 # Operator settings match the new clone. No private client settings are printed.
 settings=json.loads((ROOT/'clone-settings.private.json').read_text())
 settings.update(release_public_key_file=str(operator/'release-signing.pub'),shared_code_file=str(operator/'shared-code'),tls_certificate_file=str(operator/'server.crt'))
 save(ROOT/'publisher-settings.private.json',settings)
 result=Publisher(private,LocalSettings(ROOT/'publisher-settings.private.json'),operator/'release-signing.key').publish(manifest,registration)
 s.update(phase='PUBLISHED',release=result);save(STATE,s);print(json.dumps(result))

def release():
 s=json.loads(STATE.read_text());assert s['phase']=='PUBLISHED'
 d=cli('drain-status');assert d['paused'] and d['generation']==s['generation'] and d['managed_requests_drained']
 w=api('/v2/workers/6');assert w['state']=='maintenance' and not w['unreachable'] and api('/v2/models/17')['replicas']==0
 native_maintenance(s['worker'].get('maintenance'));s['phase']='WORKER_RELEASED';save(STATE,s)
 end=time.monotonic()+60
 while api('/v2/workers/6')['state']!='ready':
  if time.monotonic()>end:raise RuntimeError('Not ready; keep admission paused')
  time.sleep(1)
 print(json.dumps({'phase':s['phase'],'generation':s['generation']}))

def create(resume_before_model=False):
 s=json.loads(STATE.read_text());assert s['phase']==('CREATING_MODEL' if resume_before_model else 'WORKER_RELEASED')
 release=json.loads((REG/'release.json').read_text())
 name='maas-ocr-registry-r1';backend_name='MaaS-OCR-registry-r1-custom'
 if not resume_before_model:assert not any(b['backend_name']==backend_name for b in api('/v2/inference-backends')['items'])
 assert not any(m['name']==name for m in api('/v2/models')['items'])
 old=next(b for b in api('/v2/inference-backends')['items'] if b['backend_name']=='MaaS-OCR-P2-R2-custom')
 cfg={**old['version_configs']['p2-r2'],'image_name':release['canonical_image']}
 if not resume_before_model:
  s['phase']='CREATING_BACKEND';save(STATE,s)
  b=api('/v2/inference-backends',{'backend_name':backend_name,'health_check_path':old['health_check_path'],
          'default_version':'registry-r1','version_configs':{'registry-r1':cfg}})
  s.update(phase='CREATING_MODEL',backend_id=b['id']);save(STATE,s)
 else:
  b=api('/v2/inference-backends/'+str(s['backend_id']));assert b['backend_name']==backend_name
 w=api('/v2/workers/6');selector={k:w['labels'][k] for k in ['os','arch']}
 assert selector['os']=='linux' and selector['arch']=='amd64'
 m=api('/v2/models',{'name':name,'source':'local_path','local_path':'/srv/maas/models/image-contained/model','cluster_id':3,
       'replicas':1,'categories':['llm'],'placement_strategy':'spread','backend':backend_name,'backend_version':'registry-r1',
       'worker_selector':selector,'cpu_offloading':False,'distributed_inference_across_workers':False,
       'env':{'GPUSTACK_MODEL_VRAM_CLAIM':str(2*1024**3)}})
 s.update(phase='CREATING_ROUTE',model_id=m['id'],selector=selector);save(STATE,s)
 route=next((r for r in api('/v2/model-routes')['items'] if r['name']==name),None)
 if route is None:route=api('/v2/model-routes',{'name':name,'categories':['llm'],'access_policy':'authed','targets':[{'model_id':m['id'],'weight':100}]})
 target={'phase':'CREATED','worker_id':6,'worker_name':WORKER,'cluster_id':3,'backend_id':b['id'],'model_id':m['id'],'route_id':route['id']}
 save(ROOT/'target.json',target);s.update(phase='CREATED',route_id=route['id']);save(STATE,s)
 print(json.dumps({**target,'selector':selector,'image':release['canonical_image']}))

if __name__=='__main__':
 assert socket.gethostname()=='XZ-31-001' and os.getuid()==0
 p=argparse.ArgumentParser();p.add_argument('phase',choices=['prepare','publish','release','create','create-model']);a=p.parse_args()
 if a.phase=='create-model':create(resume_before_model=True)
 else:globals()[a.phase]()
