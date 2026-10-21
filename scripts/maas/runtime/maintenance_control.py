"""001 host-only operator command. No management key enters the task containers.

Config mode600 supplies task CLI argv and the forced-command node SSH argv.
Use fresh journal path for each maintenance window. Recovery never resumes tasks.
"""
import argparse,fcntl,json,os,subprocess,sys
from pathlib import Path
ROOT=Path('/root/wtaobj/maas-framework')
sys.path.insert(0,str(ROOT));sys.path.insert(0,str(ROOT/'scripts/maas/p0'))
from common import admin
from maas_platform.node_maintenance import NodeMaintenance
from maas_platform.operator_config import private_json,wait_collector


def main():
    p=argparse.ArgumentParser();p.add_argument('--config',required=True);p.add_argument('action',choices=['begin','stop','recover','resume','status']);p.add_argument('--reason',default='planned 002 maintenance');a=p.parse_args()
    path=Path(a.config)
    if path.stat().st_mode & 0o077:raise RuntimeError('Operator config must be mode600')
    cfg=private_json(path)
    def execute(args):
        return subprocess.check_output(args,text=True,stderr=subprocess.PIPE,timeout=90)
    def tasks(action,reason=None):
        if action=='status':args=['drain-status']
        elif action=='pause':args=['pause','--reason',reason]
        else:
            # The runtime collector must be ready again before a real recovery probe.
            if cfg.get('collector_health_file'):wait_collector(cfg['collector_health_file'],cfg.get('wait_seconds',180))
            args=['resume','--credential-id',cfg['credential_id'],'--probe-image',cfg['probe_image']]
        return json.loads(execute(cfg['task_cli']+args))
    def node(action):return json.loads(execute(cfg['node_ssh']+[action]))
    controller=NodeMaintenance(admin,tasks,node,cfg['journal'],wait_seconds=cfg.get('wait_seconds',180))
    lock=Path(cfg['journal']+'.lock');lock.parent.mkdir(mode=0o700,parents=True,exist_ok=True)
    fd=os.open(lock,os.O_CREAT|os.O_RDWR,0o600)
    with os.fdopen(fd,'w') as f:
        fcntl.flock(f,fcntl.LOCK_EX|fcntl.LOCK_NB)
        if a.action=='begin':
            if Path(cfg['journal']).exists():raise RuntimeError('Session already started; inspect/recover its journal')
            if cfg.get('recovery_unit'):
                execute(['systemd-run','--quiet','--unit',cfg['recovery_unit'],'--on-active=480s',
                    '/usr/bin/python3',str(Path(__file__).resolve()),'--config',str(path.resolve()),'recover'])
            value=controller.begin(cfg['worker_id'],cfg['model_ids'],a.reason)
        elif a.action=='status':value=controller.state()
        else:value=getattr(controller,a.action)()
        if a.action=='resume' and cfg.get('recovery_unit'):
            execute(['systemctl','stop',cfg['recovery_unit']+'.timer'])
    print(json.dumps(value))


if __name__=='__main__':main()
