"""Controlled E6 rollout. Run on 001; preserves DB, artifacts, GPUStack and models.

Failure leaves admission paused. Private rollback directory retains old source,
configuration and collector assets; never upload it or print its contents.
"""
import json,os,shutil,subprocess,time
from pathlib import Path
ROOT=Path('/root/wtaobj/maas-framework');LIVE=Path('/root/wtaobj/maas-live')
CANDIDATE=Path('/root/wtaobj/maas-e6-candidate')
COMPOSE=['docker','compose','--env-file',str(LIVE/'app.env'),'-f',str(ROOT/'deploy/maas/compose.yaml')]
def run(*args):return subprocess.check_output(args,text=True,stderr=subprocess.PIPE,timeout=120)
def cli(*args):return json.loads(run(*COMPOSE,'exec','-T','api','python','-m','maas_platform.cli',*args))
def main():
    report=LIVE/'e6-deployment.json'
    if report.exists():raise RuntimeError('E6 rollout already recorded; inspect before rerunning')
    status=cli('drain-status')
    if status['paused'] or any(status[k] for k in ('active_tasks','backend_activity','sync_activity')) or cli('status')['tasks'].get('QUEUED',0):
        raise RuntimeError('Rollout requires idle, unpaused, drained baseline and empty queue')
    run('docker','image','inspect','--format','{{.Id}}','cybercafe-maas-tasks:e6-r1')
    backup=LIVE/('e6-rollback-'+time.strftime('%Y%m%dT%H%M%SZ',time.gmtime()));backup.mkdir(mode=0o700)
    for name in ('maas_platform','scripts/maas/runtime','deploy/maas'):
        shutil.copytree(ROOT/name,backup/'source'/name)
    for name in ('app.env','catalog.json','maintenance'):
        source=LIVE/name
        if source.is_dir():shutil.copytree(source,backup/name)
        else:shutil.copy2(source,backup/name)
    shutil.copytree('/root/wtaobj/maas-runtime',backup/'collector-assets')
    shutil.copy2('/etc/systemd/system/maas-collector.service',backup/'maas-collector.service')
    result={'started_at':time.time(),'rollback_directory':str(backup),'image':'cybercafe-maas-tasks:e6-r1','state':'PREPARED'}
    def write():
        fd=os.open(report,os.O_CREAT|os.O_TRUNC|os.O_WRONLY,0o600)
        with os.fdopen(fd,'w') as f:json.dump(result,f,indent=2)
    write()
    try:
        cli('pause','--reason','E6 collector retirement rollout')
        assert cli('drain-status')['managed_requests_drained']
        run(*COMPOSE,'stop','--timeout','30','api','executor','observer')
        for name in ('maas_platform','scripts/maas/runtime','deploy/maas'):
            shutil.copytree(CANDIDATE/name,ROOT/name,dirs_exist_ok=True)
        # Keep catalog bytes unchanged: queued-task fingerprints and revision stay stable.
        env=LIVE/'app.env';lines=env.read_text().splitlines()
        lines=[s for s in lines if not s.startswith(('MAAS_TASK_IMAGE=','MAAS_TRUSTED_DIR='))]
        env.write_text('\n'.join(lines+['MAAS_TASK_IMAGE=cybercafe-maas-tasks:e6-r1'])+'\n');env.chmod(0o600)
        p=LIVE/'maintenance/binding.json';cfg=json.loads(p.read_text());cfg.pop('collector_health_file',None);p.write_text(json.dumps(cfg));p.chmod(0o600)
        run(*COMPOSE,'up','-d','--no-deps','api','executor','observer')
        for _ in range(30):
            try:
                assert cli('drain-status')['paused'];break
            except Exception:time.sleep(1)
        else:raise RuntimeError('API failed to restart paused')
        run('systemctl','disable','--now','maas-collector.service')
        run('systemctl','mask','--runtime','maas-collector.service')
        result['collector_stopped']=run('systemctl','show','maas-collector.service','-p','ActiveState','--value').strip()=='inactive'
        assert result['collector_stopped']
        cli('resume','--credential-id','pilot-ocr','--probe-image','/run/maas-probe/upright.png')
        result['state']='DEPLOYED';result['finished_at']=time.time();write()
        print(json.dumps(result))
    except Exception:
        result['state']='FAILED_REQUIRES_OPERATOR_REVIEW';write()
        # Never resume after an uncertain deployment or replay an unknown request.
        raise RuntimeError('E6 rollout failed; inspect private rollback and paused/UNKNOWN state') from None
if __name__=='__main__':main()
