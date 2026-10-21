"""001: bind existing live pilot once, allocate a fresh private session per window."""
import argparse,json,os,socket,subprocess,sys,time
from pathlib import Path
ROOT=Path('/root/wtaobj/maas-framework');LIVE=Path('/root/wtaobj/maas-live');OPS=LIVE/'maintenance'
sys.path.insert(0,str(ROOT))
from maas_platform.operator_config import create_private,new_session


def main():
    p=argparse.ArgumentParser();p.add_argument('action',choices=['bind','new-session']);a=p.parse_args()
    if os.getuid()!=0 or socket.gethostname()!='XZ-31-001':raise RuntimeError('001 root operator only')
    if a.action=='new-session':print(str(new_session(OPS)));return
    sys.path.insert(0,str(ROOT/'scripts/maas/p0'))
    from common import admin
    if admin('/version').get('version') not in ('2.2.3','v2.2.3'):raise RuntimeError('Approved GPUStack v2.2.3 required')
    if not (LIVE/'identity.json').is_file():raise RuntimeError('Live pilot identity missing')
    health=Path('/root/wtaobj/maas-runtime/health.json');v=json.loads(health.read_text())
    assert v['ok'] and time.time()-v['updated_at']<15
    access=Path('/root/wtaobj/maas-maintenance-access')
    for file in ('id_ed25519','known_hosts'):
        if not (access/file).is_file():raise RuntimeError('Dedicated maintenance access is missing')
    task_cli=['docker','compose','--env-file',str(LIVE/'app.env'),'-f',str(ROOT/'deploy/maas/compose.yaml'),
        'exec','-T','api','python','-m','maas_platform.cli']
    status=json.loads(subprocess.check_output(task_cli+['drain-status'],text=True,stderr=subprocess.PIPE,timeout=30))
    if status['paused'] or status['active_tasks'] or status['backend_activity'] or status['sync_activity']:
        raise RuntimeError('Bind only from an idle healthy live baseline')
    worker=admin('/v2/workers/2');assert worker['name']=='XZ-31-002' and worker['state']=='ready'
    models=[];page=1
    while True:
        rows=admin('/v2/models?perPage=100&page='+str(page))['items'];models.extend(rows)
        if len(rows)<100:break
        page+=1
    ids=sorted(m['id'] for m in models if m.get('cluster_id')==worker['cluster_id'])
    # The binding is intentionally limited to the already accepted two-model pilot.
    if ids!=[1,9]:raise RuntimeError('Cluster model set differs from approved pilot; review maintenance scope')
    cfg={'worker_id':2,'model_ids':ids,'credential_id':'pilot-ocr','probe_image':'/run/maas-probe/upright.png',
        'task_cli':task_cli,'collector_health_file':str(health),'wait_seconds':180,
        'node_ssh':['ssh','-F','/dev/null','-i',str(access/'id_ed25519'),'-o','IdentitiesOnly=yes',
            '-o','BatchMode=yes','-o','StrictHostKeyChecking=yes','-o','UserKnownHostsFile='+str(access/'known_hosts'),
            '-o','ConnectTimeout=5','-o','ClearAllForwardings=yes','-o','ForwardAgent=no','-o','RequestTTY=no','root@10.11.31.2']}
    OPS.mkdir(mode=0o700,exist_ok=True)
    create_private(OPS/'binding.json',cfg)
    print(json.dumps({'bound':True,'worker_id':2,'model_ids':ids,'live_project':'pilot-ocr','config':str(OPS/'binding.json')}))


if __name__=='__main__':main()
