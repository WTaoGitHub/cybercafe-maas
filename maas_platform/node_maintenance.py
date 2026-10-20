"""Private v2.2.3 global-replica maintenance coordinator, not a poweroff API.

Callbacks carry operator authority outside customer API/executors. The durable
journal contains fingerprints and original replica/maintenance values, not keys.
"""
import json,os,time,uuid
from pathlib import Path
from .collector import atomic_json
from .versioning import MODEL_FIELDS,digest


def model_hash(model):return digest({k:model.get(k) for k in MODEL_FIELDS})


class NodeMaintenance:
    def __init__(self,api,tasks,node,journal,wait_seconds=180,sleep=time.sleep):
        self.api,self.tasks,self.node=api,tasks,node
        self.path=Path(journal);self.wait_seconds=wait_seconds;self.sleep=sleep

    def state(self):return json.loads(self.path.read_text())

    def event(self,phase,**values):
        state=self.state();state.update(values,phase=phase)
        state['events'].append({'phase':phase,'at':time.time()})
        atomic_json(self.path,state);return state

    def models(self):
        rows=[];page=1
        while True:
            batch=self.api('/v2/models?perPage=100&page='+str(page))['items'];rows.extend(batch)
            if len(batch)<100:return rows
            page+=1

    def worker(self):return self.api('/v2/workers/'+str(self.state()['worker']['id']))

    def wait(self,fn):
        end=time.monotonic()+self.wait_seconds
        while time.monotonic()<end:
            value=fn()
            if value:return value
            self.sleep(1)
        raise RuntimeError('Maintenance convergence deadline reached; admission remains paused')

    def check_window(self):
        s=self.state();status=self.tasks('status')
        if not status['managed_requests_drained'] or status['generation']!=s['generation']:
            raise RuntimeError('Maintenance window changed or managed activity is not drained')
        if s['phase'] in ('MAINTENANCE_ENABLED','SCALING_DOWN','REPLICAS_ZERO','STOPPED','STOP_UNCONFIRMED'):
            if self.worker().get('maintenance')!=s['maintenance_value']:
                raise RuntimeError('Worker maintenance ownership changed')
        return status

    def check_models(self):
        s=self.state();rows=[m for m in self.models() if m.get('cluster_id')==s['cluster_id']]
        if {m['id'] for m in rows}!={m['id'] for m in s['models']}:
            raise RuntimeError('Cluster model set changed; refusing unscoped changes')
        originals={m['id']:m for m in s['models']}
        for m in rows:
            old=originals[m['id']]
            if model_hash(m)!=old['sha256'] or m['replicas'] not in (0,old['replicas']):
                raise RuntimeError('Deployment drift detected; do not overwrite operator changes')
        return rows

    def begin(self,worker_id,model_ids,reason):
        if self.path.exists():raise RuntimeError('Journal already exists; inspect/recover it instead of overwriting')
        worker=self.api('/v2/workers/'+str(worker_id))
        if worker['name']!='XZ-31-002' or worker['state']!='ready' or (worker.get('maintenance') or {}).get('enabled'):
            raise RuntimeError('Only the ready existing 002 worker can enter this prototype')
        rows=[m for m in self.models() if m.get('cluster_id')==worker['cluster_id']]
        if {m['id'] for m in rows}!=set(model_ids) or not rows:raise RuntimeError('Explicit model list must cover the entire target cluster')
        ident=uuid.uuid4().hex
        state={'session':ident,'phase':'PREPARED','cluster_id':worker['cluster_id'],'events':[],
            'maintenance_value':{'enabled':True,'message':'maas:'+ident+':'+reason},
            'worker':{k:worker.get(k) for k in ('id','name','labels','maintenance')},
            'models':[{'id':m['id'],'name':m['name'],'replicas':m['replicas'],'sha256':model_hash(m)} for m in rows]}
        self.path.parent.mkdir(mode=0o700,parents=True,exist_ok=True)
        fd=os.open(self.path,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
        with os.fdopen(fd,'w') as f:json.dump(state,f);f.flush();os.fsync(f.fileno())
        self.tasks('pause',reason)
        status=self.tasks('status');self.event('PAUSED',generation=status['generation'])
        def drained():
            value=self.tasks('status')
            if any(x['state']=='UNKNOWN' for x in value.get('backend_activity',[])+value.get('sync_activity',[])):
                raise RuntimeError('Managed activity is not drained: UNKNOWN requires actual termination evidence')
            return value if value['managed_requests_drained'] else None
        self.wait(drained);self.check_window();self.check_models()
        self.node('arm')  # independent worker/agent rollback timer before mutations
        self.event('ARMED')
        self.set_maintenance(state['maintenance_value'])
        # Capture the key distinction: maintenance can coexist with running instances.
        instances={str(m['id']):self.api('/v2/models/'+str(m['id'])+'/instances')['items'] for m in rows}
        self.event('MAINTENANCE_ENABLED',instances_after_maintenance={k:[{'id':x['id'],'state':x['state'],'worker_name':x.get('worker_name')} for x in v] for k,v in instances.items()})
        return self.state()

    def set_maintenance(self,value):
        old=self.state()['worker'];current=self.worker()
        if current['name']!=old['name'] or current['labels']!=old['labels']:
            raise RuntimeError('Worker identity/labels changed')
        if current.get('maintenance') not in (old['maintenance'],self.state()['maintenance_value']):
            raise RuntimeError('Worker maintenance was changed by another operator')
        self.api('/v2/workers/'+str(old['id']),{'name':old['name'],'labels':old['labels'],'maintenance':value},'PUT')

    def stop(self):
        if self.state()['phase'] not in ('MAINTENANCE_ENABLED','SCALING_DOWN','REPLICAS_ZERO'):
            raise RuntimeError('Stop phase is not eligible')
        self.check_window();rows=self.check_models();self.event('SCALING_DOWN')
        for m in rows:
            self.check_window();m['replicas']=0;self.api('/v2/models/'+str(m['id']),m,'PUT')
        self.wait(lambda:all(not self.api('/v2/models/'+str(m['id'])+'/instances')['items'] for m in rows))
        self.event('REPLICAS_ZERO');self.check_window();self.check_models()
        self.wait(lambda:not(set(self.node('status')['running_workload_containers'])-{'wta-gpustack-worker'}))
        evidence=self.node('stop')
        if not evidence['backend_stop_observed']:
            self.event('STOP_UNCONFIRMED',stop_evidence=evidence)
            raise RuntimeError('Node still has worker/workload/GPU activity; no stop proof')
        return self.event('STOPPED',stop_evidence=evidence)

    def recover(self):
        # Can resume after a crash/partial scale change. Never auto-releases UNKNOWN.
        if self.state()['phase'] in ('RECOVERED','RESUMED'):return self.state()
        self.check_window();self.check_models();self.event('RECOVERING')
        self.node('restore')
        original=self.state()['worker']['maintenance']
        self.set_maintenance(original)
        self.wait(lambda:self.worker()['state']=='ready')
        rows=self.check_models();originals={m['id']:m for m in self.state()['models']}
        for m in rows:
            self.check_window();m['replicas']=originals[m['id']]['replicas'];self.api('/v2/models/'+str(m['id']),m,'PUT')
        def ready():
            for m in self.check_models():
                instances=self.api('/v2/models/'+str(m['id'])+'/instances')['items']
                if len(instances)!=originals[m['id']]['replicas'] or any(x['state']!='running' for x in instances):return False
            return True
        self.wait(ready)
        return self.event('RECOVERED')

    def resume(self):
        if self.state()['phase']!='RECOVERED':raise RuntimeError('Recover deployments before resuming admission')
        self.check_window();self.check_models()
        evidence=self.tasks('resume')  # existing Maintenance.resume performs real probes
        self.node('disarm')
        return self.event('RESUMED',recovery_probe=evidence)
