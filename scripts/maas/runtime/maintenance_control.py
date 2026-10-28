"""001-only schema-v2 global multi-worker maintenance; legacy bindings cannot mutate."""
import argparse,fcntl,json,os,socket,subprocess,sys
from pathlib import Path
ROOT=Path('/root/wtaobj/maas-framework')
sys.path[:0]=[str(ROOT),'/root/wtaobj']
from gpustack_admin import api
from maas_platform.cluster_maintenance import ClusterMaintenance
from maas_platform.operator_config import private_json


def main():
    p=argparse.ArgumentParser();p.add_argument('--config',required=True);p.add_argument('action',choices=['plan','begin','stop','recover','resume','status']);p.add_argument('--reason',default='planned global clone maintenance');a=p.parse_args()
    if os.getuid()!=0 or socket.gethostname()!='XZ-31-001':raise RuntimeError('001 root only')
    path=Path(a.config);cfg=private_json(path)
    if cfg.get('schema_version')!=2 or cfg.get('scope')!='cluster-global':
        if a.action=='status':print(json.dumps({'legacy_read_only':True,'journal':private_json(cfg['journal'])}));return
        raise RuntimeError('Legacy maintenance binding is stale; bind current topology before mutations')
    def execute(args):return subprocess.check_output(args,text=True,stderr=subprocess.PIPE,timeout=60)
    def tasks(action,reason=None):
        if action=='status':args=['drain-status']
        elif action=='pause':args=['pause','--reason',reason]
        else:args=['resume','--credential-id',cfg['credential_id'],'--probe-image',cfg['probe_image']]
        return json.loads(execute(cfg['task_cli']+args))
    def node(bound,action,session):
        args=bound['node_ssh']+[action]+([session] if session else [])
        return json.loads(execute(args))
    controller=ClusterMaintenance(api,tasks,node,{**cfg,'journal':cfg.get('journal','/run/maas-maintenance-plan-unused')})
    if a.action=='plan':
        value=controller.snapshot()
        print(json.dumps({'read_only':True,'replica_source':value['replica_source'],'models':value['models'],
                          'worker_ids':[w['id'] for w in value['workers']]}));return
    fd=os.open('/run/maas-global-maintenance.lock',os.O_CREAT|os.O_RDWR,0o600)
    with os.fdopen(fd,'w') as handle:
        fcntl.flock(handle,fcntl.LOCK_EX|fcntl.LOCK_NB)
        if a.action=='begin':
            if Path(cfg['journal']).exists():raise RuntimeError('Existing journal; inspect original session')
            execute(['systemd-run','--quiet','--unit',cfg['recovery_unit'],'--on-active=480s',
                     '/usr/bin/python3',str(Path(__file__).resolve()),'--config',str(path),'recover'])
            value=controller.begin(a.reason)
        elif a.action=='status':value={'journal':controller.state(),'managed':tasks('status')}
        else:value=getattr(controller,a.action)()
        if value.get('phase')=='RESUMED':execute(['systemctl','stop',cfg['recovery_unit']+'.timer'])
    print(json.dumps(value))


if __name__=='__main__':main()
