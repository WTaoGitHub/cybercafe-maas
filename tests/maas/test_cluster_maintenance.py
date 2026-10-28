import copy
import pytest
from maas_platform.cluster_maintenance import ClusterMaintenance,model_hash


class Env:
 def __init__(self,tmp_path):
  self.workers=[{'id':i,'name':'worker'+str(i),'ip':'10.11.31.'+str(i-5),'cluster_id':3,
    'labels':{'worker-name':'worker'+str(i)},'maintenance':None,'state':'ready',
    'container_id':'container'+str(i),'boot_id':'boot'+str(i)} for i in [7,8]]
  self.model={'id':18,'name':'ocr','cluster_id':3,'replicas':14,'ready_replicas':14,'env':{'claim':'2GiB'}}
  self.paused=False;self.generation=0;self.unknown=False;self.mutations=[];self.running={7:True,8:True};self.sessions={};self.resume_calls=0
  cfg={'journal':str(tmp_path/'journal.json'),'cluster_id':3,'workers':copy.deepcopy(self.workers),
       'model_ids':[18],'route_ids':[17],'model_hashes':{'18':model_hash(self.model)},'approved_replicas':{'18':14},'wait_seconds':.05}
  self.c=ClusterMaintenance(self.api,self.tasks,self.node,cfg,sleep=lambda _:None)
 def api(self,path,body=None,method=None):
  if path=='/version':return {'version':'v2.2.3'}
  if path.startswith('/v2/model-routes/'):return {'id':17,'name':'ocr','access_policy':'authed'}
  if path.startswith('/v2/model-route-targets?'):return {'items':[{'id':1,'model_id':18,'weight':100}]}
  if path.startswith('/v2/models?'):return {'items':[copy.deepcopy(self.model)]}
  if path=='/v2/workers':return {'items':copy.deepcopy(self.workers)}
  if path.startswith('/v2/workers/'):
   ident=int(path.rsplit('/',1)[1]);w=next(w for w in self.workers if w['id']==ident)
   if method=='PUT':
    self.mutations.append(path);w.update(body);w['state']='maintenance' if (w.get('maintenance') or {}).get('enabled') else 'ready'
   return copy.deepcopy(w)
  if path=='/v2/models/18/instances':
   return {'items':[{'id':i,'state':'running','worker_id':7 if i<7 else 8} for i in range(self.model['replicas'])]}
  if path=='/v2/models/18':
   if method=='PUT':self.mutations.append(path);self.model=copy.deepcopy(body)
   return copy.deepcopy(self.model)
  raise AssertionError(path)
 def tasks(self,action,reason=None):
  if action=='pause':self.paused=True;self.generation+=1
  if action=='resume':self.paused=False;self.generation+=1;self.resume_calls+=1
  return {'paused':self.paused,'generation':self.generation,'managed_requests_drained':self.paused and not self.unknown,
          'backend_activity':[{'state':'UNKNOWN'}] if self.unknown else [],'sync_activity':[]}
 def node(self,b,action,session):
  ident=b['id']
  if action=='arm':self.sessions[ident]=session
  elif action=='stop':
   assert self.model['replicas']==0;self.running[ident]=False;return {'backend_stop_observed':True}
  elif action=='restore':self.running[ident]=True
  elif action=='disarm':self.sessions.pop(ident,None)
  return {'identity_verified':True,'container_id':'container'+str(ident),'boot_id':'boot'+str(ident),
   'running_worker_containers':['maas-gpustack-worker'] if self.running[ident] else [],
   'running_workload_containers':['maas-gpustack-worker']+(['inference'] if self.model['replicas'] else []),
   'gpu_compute_processes':[],'maintenance_session':self.sessions.get(ident),'node_session_active':ident in self.sessions}


def test_global_stop_both_workers_restore14_and_probe_before_admission(tmp_path):
 e=Env(tmp_path)
 assert e.c.begin('test')['phase']=='MAINTENANCE_ENABLED'
 assert e.c.stop()['phase']=='STOPPED' and not any(e.running.values()) and e.model['replicas']==0
 assert e.c.recover()['phase']=='RECOVERED' and all(e.running.values()) and e.model['replicas']==14 and e.paused
 assert e.c.resume()['phase']=='RESUMED' and not e.paused and e.resume_calls==1 and not e.sessions


def test_unknown_never_scales_or_stops(tmp_path):
 e=Env(tmp_path);e.unknown=True
 with pytest.raises(RuntimeError,match='UNKNOWN'):e.c.begin('unknown')
 assert e.paused and not e.mutations and not e.sessions and all(e.running.values())


@pytest.mark.parametrize('change',['name','labels','replicas','model','generation'])
def test_scope_or_generation_drift_prevents_destructive_operations(tmp_path,change):
 e=Env(tmp_path);e.c.begin('test');e.mutations=[]
 if change=='name':e.workers[0]['name']='reused'
 if change=='labels':e.workers[0]['labels']={}
 if change=='replicas':e.model['replicas']=15
 if change=='model':e.model['env']['claim']='1GiB'
 if change=='generation':e.generation+=1
 with pytest.raises(RuntimeError):e.c.stop()
 assert not e.mutations and all(e.running.values())


def test_boot_identity_mismatch_refuses_before_pause(tmp_path):
 e=Env(tmp_path);original=e.c.node
 def node(b,action,session):return {**original(b,action,session),'boot_id':'new-clone'}
 e.c.node=node
 with pytest.raises(RuntimeError,match='identity'):e.c.begin('test')
 assert not e.paused and not e.mutations


def test_probe_failure_keeps_paused_and_no_false_resumed(tmp_path):
 e=Env(tmp_path);e.c.begin('test');e.c.stop();e.c.recover();old=e.c.tasks
 def tasks(action,*args):
  if action=='resume':raise RuntimeError('probe failed')
  return old(action,*args)
 e.c.tasks=tasks
 with pytest.raises(RuntimeError):e.c.resume()
 assert e.paused and e.c.state()['phase']=='RECOVERED'


def test_disarm_failure_can_finish_without_repeating_probe(tmp_path):
 e=Env(tmp_path);e.c.begin('test');e.c.stop();e.c.recover();original=e.c.node
 def node(b,action,session):
  if action=='disarm':raise RuntimeError('SSH unavailable')
  return original(b,action,session)
 e.c.node=node
 with pytest.raises(RuntimeError):e.c.resume()
 assert e.c.state()['phase']=='ADMISSION_RESUMED' and not e.paused and e.resume_calls==1
 e.c.node=original
 assert e.c.resume()['phase']=='RESUMED' and e.resume_calls==1
