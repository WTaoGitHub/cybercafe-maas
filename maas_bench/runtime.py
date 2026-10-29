"""Controller-local read-only experiment fence; not a scheduling component."""
import hashlib
import importlib
from pathlib import Path
import sys

from .contracts import Parameters


class ControllerProbe:
    def __init__(self,root,model_id,profile_id,input_sha256):
        self.root=Path(root);self.model_id=model_id;self.profile_id=profile_id;self.inputs=input_sha256
        sys.path.insert(0,str(self.root/'scripts/maas/runtime'))
        self.pool=importlib.import_module('executor_pool')
        sys.path.insert(0,'/root/wtaobj')
        self.api=importlib.import_module('gpustack_admin').api

    def capture(self):
        pool=self.pool;api=self.api
        apps=pool.inventory();status=pool.cli('drain-status')
        if status['paused'] or any(r['state']=='UNKNOWN' for r in status['backend_activity']+status['sync_activity']):
            raise RuntimeError('Paused or UNKNOWN; no experiment')
        model=api('/v2/models/'+str(self.model_id));instances=api('/v2/models/'+str(self.model_id)+'/instances')['items']
        if len(instances)!=model['replicas'] or any(i['state']!='running' for i in instances):
            raise RuntimeError('Native instances not converged')
        workers=[]
        for ident in sorted({r['worker_id'] for r in instances}):
            worker=api('/v2/workers/'+str(ident))
            if worker['state']!='ready':raise RuntimeError('Worker not ready')
            workers.append({k:worker.get(k) for k in ('id','cluster_id','name','ip','labels')})
            device_status=worker.get('status') or {}
            workers[-1]['gpus']=[{**{k:d.get(k) for k in ('uuid','name','driver_version','runtime_version')},
                'memory_total':(d.get('memory') or {}).get('total')} for d in device_status.get('gpu_devices',[])]
            if not workers[-1]['gpus']:raise RuntimeError('Missing native GPU identity evidence')
            workers[-1]['cpu_total']=(device_status.get('cpu') or {}).get('total')
            workers[-1]['ram_total']=(device_status.get('memory') or {}).get('total')
        app=apps[0];claim=int(model['env']['GPUSTACK_MODEL_VRAM_CLAIM'])
        parameters=Parameters(model['replicas'],sum(a['service']=='executor' for a in apps),app['global_limit'],app['project_limit'],app['global_queue'],app['project_queue'],claim)
        text=(pool.LIVE/'app.env').read_text()
        values=dict(line.split('=',1) for line in text.splitlines() if '=' in line and not line.startswith('#'))
        catalog=Path(values['MAAS_CATALOG_PATH'].strip('"\''))
        changeable={'MAAS_EXECUTOR_REPLICAS','MAAS_MAX_RUNNING','MAAS_MAX_PROJECT_RUNNING','MAAS_MAX_QUEUED','MAAS_MAX_PROJECT_QUEUED'}
        from .snapshot import digest
        normalized={k:v for k,v in model.items() if k not in ('ready_replicas','created_at','updated_at','has_stale_lora_instances')}
        return {'profile_id':self.profile_id,'input_sha256':self.inputs,'model':normalized,
            'parameters':parameters.to_dict(),'generation':status['generation'],
            'gpustack_version':api('/version')['version'],'workers':workers,
            'instances':sorted(({'id':r['id'],'worker_id':r['worker_id']} for r in instances),key=lambda r:r['id']),
            'app_containers':sorted(apps,key=lambda a:a['name']),
            'app_env_sha256':hashlib.sha256(text.encode()).hexdigest(),
            'app_fixed_env_sha256':digest({k:v for k,v in values.items() if k not in changeable}),
            'catalog_sha256':hashlib.sha256(catalog.read_bytes()).hexdigest()}

    def resources(self,snapshot):
        import httpx
        from benchmark_executors import worker_metrics
        names=['worker_node_gpu_utilization_rate','worker_node_gram_used_bytes',
            'worker_node_cpu_utilization_rate','worker_node_memory_used_bytes','worker_node_gpu_temperature_celsius']
        rows=[]
        with httpx.Client(timeout=2,trust_env=False,follow_redirects=False) as client:
            for worker in snapshot['workers']:
                response=client.get('http://'+worker['ip']+':10151/metrics')
                if response.status_code!=200:raise RuntimeError('Native resource metrics unavailable')
                rows.append({'worker_id':worker['id'],**worker_metrics(response.text.splitlines(),names)})
        return rows
