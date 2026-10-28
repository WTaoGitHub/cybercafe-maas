"""Global two/multi-worker maintenance, explicitly distinct from E1 local draining."""
import hashlib
import json
import os
from pathlib import Path
import time
import uuid
from collections import Counter


def fingerprint(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True,separators=(',',':')).encode()).hexdigest()


def model_hash(model):
    # Exclude volatile status/replica counts only; runtime image/command/env/permissions are covered.
    return fingerprint({k:v for k,v in model.items() if k not in
                        ('replicas','ready_replicas','updated_at','created_at','has_stale_lora_instances')})


def save(path,value):
    path=Path(path);tmp=path.with_suffix('.tmp')
    with tmp.open('w') as f:
        os.fchmod(f.fileno(),0o600);json.dump(value,f,indent=2);f.flush();os.fsync(f.fileno())
    os.replace(tmp,path)


class ClusterMaintenance:
    def __init__(self,api,tasks,node,cfg,sleep=time.sleep):
        self.api,self.tasks,self.node,self.cfg=api,tasks,node,cfg
        self.path=Path(cfg['journal']);self.sleep=sleep

    def state(self):return json.loads(self.path.read_text())

    def event(self,phase,**values):
        s=self.state();s.update(values,phase=phase)
        s['events'].append({'phase':phase,'at':time.time()});save(self.path,s);return s

    def all_models(self):
        rows=[];page=1
        while True:
            batch=self.api('/v2/models?perPage=100&page='+str(page))['items'];rows.extend(batch)
            if len(batch)<100:return rows
            page+=1

    def wait(self,fn):
        end=time.monotonic()+self.cfg.get('wait_seconds',180)
        while time.monotonic()<end:
            if fn():return
            self.sleep(1)
        raise RuntimeError('Maintenance convergence deadline; admission remains paused')

    def worker(self,bound):
        w=self.api('/v2/workers/'+str(bound['id']))
        for k in ('id','name','cluster_id','ip','labels'):
            if w.get(k)!=bound.get(k):raise RuntimeError('Worker identity/topology drift')
        return w

    def models(self,baseline=False):
        if self.api('/version').get('version') not in ('2.2.3','v2.2.3'):raise RuntimeError('Unapproved GPUStack version')
        rows=[m for m in self.all_models() if m['cluster_id']==self.cfg['cluster_id']]
        if {m['id'] for m in rows}!=set(self.cfg['model_ids']):raise RuntimeError('Cluster model scope changed')
        if not baseline:
            originals={m['id']:m for m in self.state()['models']}
            for m in rows:
                old=originals[m['id']]
                if model_hash(m)!=old['sha256'] or m['replicas'] not in (0,old['replicas']):
                    raise RuntimeError('Model configuration/replica drift; refuse overwrite')
        return rows

    def window(self):
        s=self.state();status=self.tasks('status')
        if not status['managed_requests_drained'] or status['generation']!=s['generation']:
            raise RuntimeError('Maintenance generation changed or requests/UNKNOWN remain')
        for bound in s['workers']:
            w=self.worker(bound)
            if w.get('maintenance') not in (bound['maintenance'],s['maintenance_value']):
                raise RuntimeError('Worker maintenance ownership changed')
        self.check_routes()
        return status

    def route_hashes(self):
        result={}
        for ident in self.cfg.get('route_ids',[]):
            route=self.api('/v2/model-routes/'+str(ident))
            targets=self.api('/v2/model-route-targets?route_id='+str(ident))['items']
            result[str(ident)]=fingerprint({'route':{k:route.get(k) for k in
                ('id','name','owner_principal_id','generic_proxy','access_policy')},
                'targets':sorted([{k:r.get(k) for k in ('id','model_id','provider_id','weight','overridden_model_name','fallback_status_codes')} for r in targets],key=lambda x:x['id'])})
        return result

    def check_routes(self):
        if self.route_hashes()!=self.state()['route_hashes']:raise RuntimeError('Route configuration drift')

    def set_worker(self,bound,value):
        self.worker(bound)
        self.api('/v2/workers/'+str(bound['id']),{'name':bound['name'],'labels':bound['labels'],'maintenance':value},'PUT')

    def begin(self,reason):
        if self.path.exists():raise RuntimeError('Journal exists; inspect original session')
        rows=self.models(baseline=True)
        bounds=self.cfg['workers'];ids={b['id'] for b in bounds}
        active={w['id'] for w in self.api('/v2/workers')['items'] if w.get('cluster_id')==self.cfg['cluster_id'] and w['state']=='ready'}
        if active!=ids:raise RuntimeError('Ready worker set differs from approved binding')
        for b in bounds:
            w=self.worker(b)
            if w['state']!='ready' or (w.get('maintenance') or {}).get('enabled'):raise RuntimeError('Worker not healthy at baseline')
            evidence=self.node(b,'status',None)
            if not evidence.get('identity_verified') or evidence['container_id']!=b['container_id'] or evidence['boot_id']!=b['boot_id']:
                raise RuntimeError('Actual clone identity differs from binding')
        models=[]
        for m in rows:
            if self.cfg.get('model_hashes',{}).get(str(m['id']))!=model_hash(m) or self.cfg['approved_replicas'][str(m['id'])]!=m['replicas']:raise RuntimeError('Model changed since binding approval')
            instances=self.api('/v2/models/'+str(m['id'])+'/instances')['items']
            if len(instances)!=m['replicas'] or any(i['state']!='running' or i['worker_id'] not in ids for i in instances):
                raise RuntimeError('Baseline instances must be healthy on approved workers')
            models.append({'id':m['id'],'name':m['name'],'replicas':m['replicas'],'sha256':model_hash(m),
                           'placement':dict(Counter(str(i['worker_id']) for i in instances))})
        if self.cfg.get('approved_route_hashes') and self.route_hashes()!=self.cfg['approved_route_hashes']:raise RuntimeError('Route changed since binding approval')
        ident=uuid.uuid4().hex
        self.path.parent.mkdir(mode=0o700,parents=True,exist_ok=True)
        state={'phase':'PREPARED','session':ident,'models':models,'workers':bounds,'events':[],
               'route_hashes':self.route_hashes(),'maintenance_value':{'enabled':True,'message':'maas-global:'+ident+':'+reason}}
        with self.path.open('x') as f:os.fchmod(f.fileno(),0o600);json.dump(state,f);f.flush();os.fsync(f.fileno())
        self.tasks('pause',reason);status=self.tasks('status');self.event('PAUSED',generation=status['generation'])
        def drained():
            v=self.tasks('status')
            if any(r['state']=='UNKNOWN' for r in v.get('backend_activity',[])+v.get('sync_activity',[])):
                raise RuntimeError('UNKNOWN requires evidence; no deployment changes allowed')
            return v['managed_requests_drained']
        self.wait(drained);self.window();self.models()
        for b in bounds:self.node(b,'arm',ident)
        self.event('ARMED')
        for b in bounds:self.window();self.set_worker(b,state['maintenance_value'])
        return self.event('MAINTENANCE_ENABLED')

    def stop(self):
        s=self.state()
        if s['phase'] not in ('MAINTENANCE_ENABLED','SCALING_DOWN','REPLICAS_ZERO'):raise RuntimeError('Invalid stop phase')
        self.window();rows=self.models();self.event('SCALING_DOWN')
        for m in rows:
            self.window();m['replicas']=0;self.api('/v2/models/'+str(m['id']),m,'PUT')
        self.wait(lambda:all(not self.api('/v2/models/'+str(m['id'])+'/instances')['items'] for m in rows))
        self.event('REPLICAS_ZERO')
        proofs={}
        for b in s['workers']:
            self.window();self.models()
            def empty():
                v=self.node(b,'status',None)
                return not(set(v['running_workload_containers'])-{'maas-gpustack-worker'} or v['gpu_compute_processes'])
            self.wait(empty)
            proofs[str(b['id'])]=self.node(b,'stop',s['session'])
            if not proofs[str(b['id'])]['backend_stop_observed']:
                self.event('STOP_UNCONFIRMED',proofs=proofs);raise RuntimeError('Actual GPU stop not proven')
        return self.event('STOPPED',proofs=proofs)

    def recover(self):
        s=self.state()
        if s['phase'] in ('RECOVERED','RESUMED'):return s
        self.window();self.models();self.event('RECOVERING')
        for b in s['workers']:
            # A partially completed arm is reconciled by actual per-node session state.
            evidence=self.node(b,'status',None)
            if evidence.get('node_session_active') and evidence.get('maintenance_session')==s['session']:
                self.node(b,'restore',s['session'])
            elif not evidence['running_worker_containers']:
                raise RuntimeError('Unarmed worker is stopped; manual review required')
            self.set_worker(b,b['maintenance'])
        self.wait(lambda:all(self.worker(b)['state']=='ready' for b in s['workers']))
        originals={m['id']:m for m in s['models']}
        for m in self.models():
            self.window();m['replicas']=originals[m['id']]['replicas'];self.api('/v2/models/'+str(m['id']),m,'PUT')
        def ready():
            for m in self.models():
                rows=self.api('/v2/models/'+str(m['id'])+'/instances')['items']
                if len(rows)!=originals[m['id']]['replicas'] or any(i['state']!='running' for i in rows):return False
                placement=dict(Counter(str(i['worker_id']) for i in rows))
                if placement!=originals[m['id']]['placement']:raise RuntimeError('Restored distribution differs; admission remains paused for review')
            return True
        self.wait(ready)
        return self.event('RECOVERED')

    def resume(self):
        s=self.state()
        if s['phase']=='RECOVERED':
            self.window();self.models()
            for b in s['workers']:
                v=self.node(b,'status',None)
                if not v.get('identity_verified') or not v['running_worker_containers']:raise RuntimeError('Worker unavailable before resume')
            evidence=self.tasks('resume')
            self.event('ADMISSION_RESUMED',probe=evidence,
                       resumed_generation=self.tasks('status')['generation'])
        elif s['phase']!='ADMISSION_RESUMED':
            raise RuntimeError('Recover deployments before resume')
        s=self.state();status=self.tasks('status')
        if status['paused'] or status['generation']!=s['resumed_generation']:
            raise RuntimeError('Admission changed after recovery probe')
        self.models();self.check_routes()
        for b in s['workers']:
            v=self.node(b,'status',None)
            if v.get('node_session_active'):
                if v.get('maintenance_session')!=s['session']:raise RuntimeError('Another node session is active')
                self.node(b,'disarm',s['session'])
        return self.event('RESUMED')
