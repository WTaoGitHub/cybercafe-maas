"""001 current multi-clone maintenance acceptance. Pauses cluster inference, preserves live data.
Uses the allocated formal session, existing identity and named volumes; no test API.
"""
import base64,json,os,subprocess,time,urllib.request,urllib.error,uuid
from pathlib import Path
ROOT=Path('/root/wtaobj/maas-framework');LIVE=Path('/root/wtaobj/maas-live');OPS=LIVE/'maintenance'
COMPOSE=['docker','compose','--env-file',str(LIVE/'app.env'),'-f',str(ROOT/'deploy/maas/compose.yaml')]


def run(*args,timeout=90):return subprocess.check_output(args,text=True,stderr=subprocess.PIPE,timeout=timeout)
def save(path,value):
    fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_TRUNC,0o600)
    with os.fdopen(fd,'w') as f:json.dump(value,f,indent=2);f.write('\n');f.flush();os.fsync(f.fileno())


def main():
    active=json.loads((OPS/'active.json').read_text());config=Path(active['config']);cfg=json.loads(config.read_text())
    journal=Path(cfg['journal']);folder=config.parent
    if journal.exists():raise RuntimeError('Use a new session for a new exercise; inspect an existing journal instead')
    key=(LIVE/'secrets/pilot-key').read_text().strip();report={'session':active['session'],'started_at':time.time(),
        'scope':'live Compose, original task DB and volumes; cluster-wide inference pause; no host shutdown'}
    def checkpoint():save(folder/'acceptance.json',report)
    def cli(*args):return json.loads(run(*cfg['task_cli'],*args))
    def control(action):
        value=json.loads(run('python3',str(ROOT/'scripts/maas/runtime/maintenance_control.py'),'--config',str(config),action,timeout=420))
        report['phase']=value['phase'];checkpoint();return value
    def request(path,body=None,media='application/json',idem=None,direct=False):
        headers={'Authorization':'Bearer '+key,'Content-Type':media}
        if idem:headers['Idempotency-Key']=idem
        raw=json.dumps(body).encode() if isinstance(body,dict) else body
        try:
            with urllib.request.urlopen(urllib.request.Request('http://127.0.0.1:'+('18830' if direct else '18831')+path,data=raw,headers=headers),timeout=40) as r:return r.status,json.load(r)
        except urllib.error.HTTPError as e:return e.code,json.load(e)
    def wait_task(ident):
        end=time.monotonic()+120
        while time.monotonic()<end:
            code,row=request('/maas/v1/tasks/'+ident);assert code==200
            if row['state'] in ('SUCCEEDED','FAILED','CANCELLED'):
                assert row['state']=='SUCCEEDED',row['state'];return row
            time.sleep(.5)
        raise RuntimeError('Queued live task did not complete')
    def counts():
        usage=cli('alerts')['usage']
        return {'tasks':sum(int(x['quantity']) for x in usage['tasks']),'sync':sum(int(x['quantity']) for x in usage['sync'])}
    def workloads():
        ids=run(*COMPOSE,'ps','-aq').split();rows=json.loads(run('docker','inspect',*ids))
        assert len(rows)>=4 and all(x['State']['Running'] for x in rows)
        return {x['Config']['Labels']['com.docker.compose.service']:x['Id'] for x in rows}
    before=cli('drain-status');assert not before['paused'] and not before['active_tasks'] and not before['backend_activity'] and not before['sync_activity']
    report['usage_before']=counts();report['containers_before']=workloads();checkpoint()
    image=(LIVE/'probes/upright.png').read_bytes()
    body={'model':'ocr.default','stream':False,'messages':[{'role':'user','content':[{'type':'image_url','image_url':{'url':'data:image/png;base64,'+base64.b64encode(image).decode()}}]}]}
    try:
        code,value=request('/v1/chat/completions',body);assert code==200 and len(json.loads(value['choices'][0]['message']['content'])['lines'])==46
        report['baseline_sync']={'status':code,'lines':46}
        code,art=request('/maas/v1/artifacts',image,'image/png');assert code==201
        taskbody={'model':'ocr.default','input':{'image':{'artifact_id':art['id']}},'timeout_seconds':600,'max_attempts':1}
        report['baseline_idempotency_key']='maint-baseline-'+active['session'];checkpoint()
        code,first=request('/maas/v1/tasks',taskbody,idem=report['baseline_idempotency_key']);assert code==202
        first=wait_task(first['id']);report['baseline_task']={'id':first['id'],'state':first['state']};checkpoint()
        start=time.monotonic();begun=control('begin');assert begun['phase']=='MAINTENANCE_ENABLED'
        report['approved_replicas']={str(m['id']):m['replicas'] for m in begun['models']}
        assert report['approved_replicas']=={'18':14}
        report['queued_idempotency_key']='maint-queued-'+active['session'];checkpoint()
        code,queued=request('/maas/v1/tasks',taskbody,idem=report['queued_idempotency_key']);assert code==202 and queued['state']=='QUEUED'
        report['queued_task']=queued['id'];checkpoint()
        stopped=control('stop');assert all(v['backend_stop_observed'] for v in stopped['proofs'].values())
        report['stop_proof']=stopped['proofs'];report['seconds_to_stop']=round(time.monotonic()-start,3);checkpoint()
        time.sleep(5)
        for bound in cfg['workers']:
            proof=json.loads(run(*bound['node_ssh'],'status'));assert proof['backend_stop_observed']
        report['held_stopped_5s']=True
        assert request('/healthz',direct=True)[0]==200
        code,value=request('/v1/chat/completions',body);assert code==503
        report['sync_during_stop']={'status':code,'code':value['error']['code']}
        code,row=request('/maas/v1/tasks/'+queued['id']);assert code==200 and row['state']=='QUEUED'
        code,value=request('/maas/v1/artifacts/'+first['result_artifact_id']);assert code==200 and len(value['outputs']['lines'])==46
        report['during_stop']={'controller_healthy':True,'queued_waited':True,'old_result_readable':True};checkpoint()
        recovered=control('recover');assert recovered['phase']=='RECOVERED' and cli('drain-status')['paused']
        report['recover_kept_admission_paused']=True;checkpoint()
        final=control('resume');assert final['phase']=='RESUMED'
        report['resume_probe']=final['probe'];report['total_window_seconds']=round(time.monotonic()-start,3)
        second=wait_task(queued['id']);assert second['attempts']==1
        code,value=request('/maas/v1/artifacts/'+second['result_artifact_id']);assert code==200 and len(value['outputs']['lines'])==46
        report['queued_after_resume']={'state':second['state'],'attempts':second['attempts'],'lines':46}
        report['containers_after']=workloads();assert report['containers_before']==report['containers_after']
        report['usage_after']=counts();report['usage_delta']={k:report['usage_after'][k]-report['usage_before'][k] for k in ('tasks','sync')}
        report['usage_delta_is_global_only']=True
        query="import json;from maas_platform.config import Settings;from maas_platform.db import Database;d=Database(Settings.from_env().database_url);\nwith d.transaction() as c: print(json.dumps(c.execute('SELECT count(*) AS n FROM task_usage_events WHERE task_id=%s', ("+repr(queued['id'])+",)).fetchone()))"
        report['queued_success_usage_events']=json.loads(run(*COMPOSE,'exec','-T','api','python','-c',query))['n']
        assert report['queued_success_usage_events']==1
        report['node_final']={str(b['id']):json.loads(run(*b['node_ssh'],'status')) for b in cfg['workers']}
        assert all(v['identity_verified'] and not v.get('node_session_active') and v['running_worker_containers'] for v in report['node_final'].values())
        status=cli('drain-status');assert not status['paused'] and not status['active_tasks'] and not status['backend_activity'] and not status['sync_activity']
        report['final_status']=status
        run(*COMPOSE,'exec','-T','observer','python','-m','maas_platform.observe','--output','/var/lib/maas/artifacts/.observability')
        report['final_alerts']=cli('alerts')['alerts'];assert not report['final_alerts']
        timer=subprocess.run(['systemctl','is-active',cfg['recovery_unit']+'.timer'],capture_output=True,text=True)
        report['controller_watchdog_active']=timer.returncode==0;assert not report['controller_watchdog_active']
        report['journal_mode']=oct(journal.stat().st_mode & 0o777);assert report['journal_mode']=='0o600'
        report['config_mode']=oct(config.stat().st_mode & 0o777);assert report['config_mode']=='0o600'
        report['ok']=True
    finally:
        if journal.exists() and json.loads(journal.read_text())['phase']!='RESUMED':
            report['requires_operator_review']=True
        report['finished_at']=time.time();checkpoint()
    print(json.dumps({'ok':report.get('ok',False),'report':str(folder/'acceptance.json'),
        'phase':report.get('phase'),'window_seconds':report.get('total_window_seconds'),'usage_delta':report.get('usage_delta')}))


if __name__=='__main__':main()
