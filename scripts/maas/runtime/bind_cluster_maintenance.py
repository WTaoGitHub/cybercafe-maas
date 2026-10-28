"""Audit and rebind current global maintenance, no replica/worker mutation."""
import argparse,json,os,socket,subprocess,sys,time
from pathlib import Path
ROOT=Path('/root/wtaobj/maas-framework');LIVE=Path('/root/wtaobj/maas-live');OPS=LIVE/'maintenance'
sys.path[:0]=[str(ROOT),'/root/wtaobj']
from gpustack_admin import api
from maas_platform.cluster_maintenance import ClusterMaintenance,model_hash
from maas_platform.operator_config import private_json


def main():
    p=argparse.ArgumentParser();p.add_argument('action',choices=['audit','bind']);p.add_argument('--workers',type=int,nargs='+',required=True);p.add_argument('--models',type=int,nargs='+',required=True);p.add_argument('--routes',type=int,nargs='+',required=True);p.add_argument('--cluster',type=int,required=True);a=p.parse_args()
    if os.getuid()!=0 or socket.gethostname()!='XZ-31-001':raise RuntimeError('001 root only')
    access=OPS/'access-v2'
    for name in ['id_ed25519','known_hosts']:
        if not (access/name).is_file():raise RuntimeError('Dedicated schema-v2 maintenance access missing')
    task_cli=['docker','compose','--env-file',str(LIVE/'app.env'),'-f',str(ROOT/'deploy/maas/compose.yaml'),'exec','-T','api','python','-m','maas_platform.cli']
    status=json.loads(subprocess.check_output(task_cli+['drain-status'],text=True,stderr=subprocess.PIPE,timeout=20))
    if status['paused'] or status['active_tasks'] or status['backend_activity'] or status['sync_activity']:raise RuntimeError('Bind only from idle healthy state')
    models=[m for m in api('/v2/models?perPage=100')['items'] if m['cluster_id']==a.cluster]
    if {m['id'] for m in models}!=set(a.models):raise RuntimeError('Explicit full cluster model list differs')
    bound=[]
    for ident in a.workers:
        w=api('/v2/workers/'+str(ident))
        if w['cluster_id']!=a.cluster or w['state']!='ready' or (w.get('maintenance') or {}).get('enabled'):raise RuntimeError('Worker baseline invalid')
        ssh=['ssh','-F','/dev/null','-i',str(access/'id_ed25519'),'-o','IdentitiesOnly=yes','-o','BatchMode=yes',
             '-o','StrictHostKeyChecking=yes','-o','UserKnownHostsFile='+str(access/'known_hosts'),'-o','ConnectTimeout=5',
             '-o','ClearAllForwardings=yes','-o','ForwardAgent=no','-o','RequestTTY=no','root@'+w['ip']]
        evidence=json.loads(subprocess.check_output(ssh+['status'],text=True,stderr=subprocess.PIPE,timeout=20))
        if not evidence['identity_verified'] or evidence['worker_id']!=ident:raise RuntimeError('Actual clone identity mismatch')
        bound.append({**{k:w.get(k) for k in ('id','name','ip','labels','maintenance','cluster_id')},
                      'boot_id':evidence['boot_id'],'container_id':evidence['container_id'],'node_ssh':ssh})
    cfg={'schema_version':2,'scope':'cluster-global','cluster_id':a.cluster,'workers':bound,'model_ids':a.models,
         'route_ids':a.routes,'model_hashes':{str(m['id']):model_hash(m) for m in models},
         'approved_replicas':{str(m['id']):m['replicas'] for m in models},'credential_id':'pilot-ocr',
         'probe_image':'/run/maas-probe/upright.png','task_cli':task_cli,'wait_seconds':180}
    probe=ClusterMaintenance(api,None,None,{**cfg,'journal':'unused'})
    cfg['approved_route_hashes']=probe.route_hashes()
    if a.action=='bind':
        active=OPS/'active.json'
        if active.exists():
            prior=private_json(private_json(active)['config'])
            if private_json(prior['journal']).get('phase')!='RESUMED':raise RuntimeError('Previous maintenance incomplete')
        tmp=OPS/'binding.v2.new';fd=os.open(tmp,os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600)
        with os.fdopen(fd,'w') as f:json.dump(cfg,f,indent=2);f.flush();os.fsync(f.fileno())
        os.replace(tmp,OPS/'binding.json')
    print(json.dumps({'action':a.action,'schema_version':2,'cluster':a.cluster,'workers':[b['id'] for b in bound],
                      'model_ids':a.models,'route_ids':a.routes,'approved_replicas':cfg['approved_replicas'],'node_identities_verified':True}))


if __name__=='__main__':main()
