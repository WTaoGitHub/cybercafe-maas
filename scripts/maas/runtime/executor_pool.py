"""001-only bounded executor scaling, paused and drained; never mutates GPUStack models."""
import argparse,fcntl,json,os,socket,subprocess,sys,time,uuid
from pathlib import Path
ROOT=Path('/root/wtaobj/maas-framework');LIVE=Path('/root/wtaobj/maas-live')
LOCK=Path('/run/maas-global-maintenance.lock')
COMPOSE=['docker','compose','--env-file',str(LIVE/'app.env'),'-f',str(ROOT/'deploy/maas/compose.yaml')]


def run(args,timeout=160):return subprocess.check_output(args,text=True,stderr=subprocess.PIPE,timeout=timeout)
def cli(*args):return json.loads(run(COMPOSE+['exec','-T','api','python','-m','maas_platform.cli',*args],60))
def save(path,value):
    temp=path.with_suffix('.new');fd=os.open(temp,os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600)
    with os.fdopen(fd,'w') as f:json.dump(value,f,indent=2);f.flush();os.fsync(f.fileno())
    os.replace(temp,path)


def inventory():
    ids=run(COMPOSE+['ps','-aq']).split();rows=json.loads(run(['docker','inspect',*ids]))
    apps=[r for r in rows if r['Config']['Labels'].get('com.docker.compose.service') in ('api','executor','observer')]
    public=[];limits=set()
    for r in apps:
        env=dict(x.split('=',1) for x in r['Config']['Env'])
        pair=(int(env['MAAS_MAX_RUNNING']),int(env['MAAS_MAX_PROJECT_RUNNING']));limits.add(pair)
        public.append({'name':r['Name'],'id':r['Id'],'service':r['Config']['Labels']['com.docker.compose.service'],
                       'image':r['Config']['Image'],'running':r['State']['Running'],'stop_timeout':r['Config'].get('StopTimeout'),
                       'global_limit':pair[0],'project_limit':pair[1]})
    if len(limits)!=1:raise RuntimeError('API/executor/observer limits disagree; do not scale')
    if not all(r['running'] for r in public):raise RuntimeError('Existing application container is stopped')
    return public


def set_count(count,limits=None):
    p=LIVE/'app.env';lines=p.read_text().splitlines();settings={'MAAS_EXECUTOR_REPLICAS':str(count)}
    if limits:settings.update(MAAS_MAX_RUNNING=str(limits[0]),MAAS_MAX_PROJECT_RUNNING=str(limits[1]))
    lines=[x for x in lines if not any(x.startswith(k+'=') for k in settings)]+[k+'='+v for k,v in settings.items()]
    temp=p.with_suffix('.pool-new');fd=os.open(temp,os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600)
    with os.fdopen(fd,'w') as f:f.write('\n'.join(lines)+'\n');f.flush();os.fsync(f.fileno())
    os.replace(temp,p)


def main():
    p=argparse.ArgumentParser();p.add_argument('action',choices=['status','scale']);p.add_argument('--replicas',type=int,choices=range(1,5));p.add_argument('--global-limit',type=int,choices=range(1,5));p.add_argument('--project-limit',type=int,choices=range(1,5));a=p.parse_args()
    if (a.global_limit is None)!=(a.project_limit is None) or (a.global_limit and a.project_limit>a.global_limit):p.error('Supply both consistent limits')
    if os.getuid()!=0 or socket.gethostname()!='XZ-31-001':raise RuntimeError('001 root only')
    if a.action=='status':print(json.dumps({'containers':inventory(),'managed':cli('drain-status')}));return
    if a.replicas is None:p.error('--replicas required')
    # Serialize with global maintenance actions and reject an unfinished maintenance window.
    with open(LOCK,'a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        active=LIVE/'maintenance/active.json'
        if active.exists():
            config=json.loads(Path(json.loads(active.read_text())['config']).read_text())
            if json.loads(Path(config['journal']).read_text())['phase']!='RESUMED':raise RuntimeError('Unfinished global maintenance')
        folder=LIVE/'executors';folder.mkdir(mode=0o700,exist_ok=True)
        pending=folder/'pending.json'
        if pending.exists() and json.loads(pending.read_text()).get('phase')!='COMPLETE':raise RuntimeError('Previous pool change incomplete; inspect before recovery')
        before=inventory();status=cli('drain-status')
        if status['paused']:raise RuntimeError('Already paused; do not take ownership of another window')
        op={'id':uuid.uuid4().hex,'phase':'PREPARED','target_replicas':a.replicas,'started_at':time.time(),'before':before,'requested_limits':[a.global_limit,a.project_limit] if a.global_limit else None}
        save(pending,op)
        try:
            paused=cli('pause','--reason','executor-pool:'+op['id']);generation=paused['generation']
            op.update(phase='PAUSED',generation=generation);save(pending,op)
            deadline=time.monotonic()+90
            while True:
                value=cli('drain-status')
                if value['generation']!=generation:raise RuntimeError('Maintenance ownership changed')
                if any(x['state']=='UNKNOWN' for x in value['backend_activity']+value['sync_activity']):raise RuntimeError('UNKNOWN; no scale/replay')
                if value['managed_requests_drained']:break
                if time.monotonic()>deadline:raise RuntimeError('Drain deadline; admission remains paused')
                time.sleep(.5)
            limits=(a.global_limit,a.project_limit) if a.global_limit else None
            set_count(a.replicas,limits)
            run(COMPOSE+['up','-d','--no-build','--no-deps']+(['api','executor','observer'] if limits else ['executor']))
            deadline=time.monotonic()+30
            while True:
                try:
                    readiness=cli('drain-status')
                    if readiness['paused'] and readiness['generation']==generation:break
                except subprocess.CalledProcessError:pass
                if time.monotonic()>deadline:raise RuntimeError('API readiness deadline; keep paused')
                time.sleep(.5)
            after=inventory();executors=[x for x in after if x['service']=='executor']
            if len(executors)!=a.replicas or any((x['stop_timeout'] or 0)<90 for x in executors):raise RuntimeError('Executor count/grace mismatch')
            if limits and any((x['global_limit'],x['project_limit'])!=limits for x in after):raise RuntimeError('Candidate limits were not applied consistently')
            op.update(phase='SCALED',after=after);save(pending,op)
            if cli('drain-status')['generation']!=generation:raise RuntimeError('Maintenance ownership changed before probe')
            op['probe']=cli('resume','--credential-id','pilot-ocr','--probe-image','/run/maas-probe/upright.png')
            op.update(phase='COMPLETE',finished_at=time.time());save(pending,op);save(folder/(op['id']+'.json'),op)
        except Exception:
            op.update(failed_at=time.time(),requires_operator_review=True);save(pending,op);raise
    print(json.dumps(op))


if __name__=='__main__':main()
