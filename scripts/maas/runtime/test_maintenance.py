"""001-only acceptance using an isolated DB/API and the retained P2 model.

No worker, Docker daemon, model, firewall or original ingress changes.
"""
import argparse,base64,json,os,shutil,socket,subprocess,sys,time,urllib.request,urllib.error,uuid
from pathlib import Path
ROOT=Path('/root/wtaobj/maas-framework');WORK=Path('/root/wtaobj/maas-p3-maintenance')
PRIVATE=ROOT/'.secrets/maintenance';DB='maas_maintenance_test';API='wta-maas-maintenance-api'
sys.path.insert(0,str(ROOT/'scripts/maas/p0'))
from common import Resources


def run(*args):return subprocess.check_output(args,text=True,stderr=subprocess.PIPE,timeout=60)
def write(path,value):
    fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_TRUNC,0o600)
    with os.fdopen(fd,'w') as f:f.write(value)


def main():
    raise RuntimeError('Historical P3 isolated collector setup retired; use current live maintenance acceptance')
    global WORK
    parser=argparse.ArgumentParser();parser.add_argument('--edge',action='store_true');parser.add_argument('--node-maintenance',action='store_true');parser.add_argument('--operations',action='store_true');options=parser.parse_args()
    if options.operations:
        if options.node_maintenance:parser.error('operations acceptance does not stop workers')
        WORK=Path('/root/wtaobj/maas-p3-operations')
    if socket.gethostname()!='XZ-31-001':raise RuntimeError('Controller 001 only')
    if PRIVATE.exists():raise RuntimeError('Prior private acceptance state exists; inspect before reuse')
    probe=socket.socket();probe.bind(('127.0.0.1',18830));probe.close()
    PRIVATE.mkdir(mode=0o700,parents=True);WORK.mkdir(mode=0o700,exist_ok=True)
    report={'scope':'temporary managed ingress and existing node002 maintenance' if options.node_maintenance else 'temporary managed ingress only',
        'original_ingress_modified':False,'through_nginx':options.edge};res=Resources();created_db=False;operator=None
    watchdog='maas-controller-recover-'+uuid.uuid4().hex[:8]
    def snapshot():
        deadline=time.monotonic()+180
        while True:
            health=json.loads(Path('/root/wtaobj/maas-runtime/health.json').read_text())
            if health['ok']:break
            if time.monotonic()>deadline:raise RuntimeError('Resident collector did not recover')
            time.sleep(1)
        for name in ('pin.json','preflight.json'):shutil.copyfile(Path('/root/wtaobj/maas-runtime')/name,WORK/name)
    def args():return ['docker','run','--rm','--network','host','--env-file',str(PRIVATE/'app.env'),'-v',str(ROOT)+':/work',
        '-v',str(WORK)+':/trusted:ro','-v','/root/wtaobj/service-validation/fixtures/upright.png:/probe.png:ro','-w','/work','python:3.11-slim']
    def cli(*command):return json.loads(run(*args(),'/work/.venv/bin/python','-m','maas_platform.cli',*command))
    def control(command):
        return json.loads(subprocess.check_output(['python3',str(ROOT/'scripts/maas/runtime/maintenance_control.py'),'--config',str(operator),command],
            text=True,stderr=subprocess.PIPE,timeout=420))
    def sql(query):return run('docker','exec','wta-maas-p1-test-db','psql','-U','maas','-d',DB,'-tAc',query).strip()
    def request(path,body=None,media='application/json',idem=None):
        raw=json.dumps(body).encode() if isinstance(body,dict) else body
        headers={'Authorization':'Bearer '+key,'Content-Type':media}
        if idem:headers['Idempotency-Key']=idem
        try:
            port=18831 if options.edge and path!='/healthz' else 18830
            with urllib.request.urlopen(urllib.request.Request('http://127.0.0.1:'+str(port)+path,data=raw,headers=headers),timeout=40) as response:
                return response.status,json.load(response)
        except urllib.error.HTTPError as exc:return exc.code,json.load(exc)
    def execute():
        code='from maas_platform.config import Settings;from maas_platform.auth import AuthConnector;from maas_platform.service import TaskService;from maas_platform.worker import Worker;s=Settings.from_env();v=TaskService(s,AuthConnector(s));print(Worker(v).run_once())'
        return run(*args(),'/work/.venv/bin/python','-c',code).strip()=='True'
    try:
        snapshot();pin=json.loads((WORK/'pin.json').read_text())
        run('docker','exec','wta-maas-p1-test-db','psql','-U','maas','-d','postgres','-c','CREATE DATABASE '+DB);created_db=True
        _,op=res.user('maintenance');_,key=res.key(op,'maintenance',[pin['route_name']],expires=1800)
        write(PRIVATE/'key',key);write(PRIVATE/'pepper',os.urandom(32).hex())
        write(PRIVATE/'bindings.json',json.dumps({'credentials':[{'id':'maintenance','project':'maintenance','secret_file':'/work/.secrets/maintenance/key','models':['ocr.default']}]}))
        write(PRIVATE/'catalog.json',json.dumps({'ocr.default':{'driver':'gpustack.ocr','revision':pin['revision'],'upstream_model':pin['route_name'],
            'max_runtime_s':25,'pin_file':'/trusted/pin.json','preflight_file':'/trusted/preflight.json'}}))
        url=(ROOT/'.secrets/test.env').read_text().strip().split('=',1)[1].replace('/maas_test','/'+DB)
        write(PRIVATE/'app.env','\n'.join(['MAAS_DATABASE_URL='+url,'MAAS_ARTIFACT_ROOT=/work/maintenance-artifacts','MAAS_GPUSTACK_URL=http://127.0.0.1:18080',
            'MAAS_BINDINGS_FILE=/work/.secrets/maintenance/bindings.json','MAAS_CATALOG_FILE=/work/.secrets/maintenance/catalog.json','MAAS_PEPPER_FILE=/work/.secrets/maintenance/pepper'])+'\n')
        a=args();run(*a[:-1],'-d','--name',API,a[-1],'/work/.venv/bin/uvicorn','maas_platform.api:create_app','--factory','--host','127.0.0.1','--port','18830')
        for _ in range(100):
            try:
                if request('/healthz')[0]==200:break
            except OSError:pass
            time.sleep(.2)
        else:raise RuntimeError('Temporary maintenance API not ready')
        if options.edge:
            denied={}
            for path in ('/','/docs','/v2/models','/token-auth','/healthz','/maas/v1/admin','/v1/models'):
                try:
                    with urllib.request.urlopen('http://127.0.0.1:18831'+path,timeout=3) as response:code=response.status
                except urllib.error.HTTPError as exc:code=exc.code
                denied[path]=code;assert code==404
            report['edge_denied_paths']=denied
            for label,req,expected in (
                ('missing-key',urllib.request.Request('http://127.0.0.1:18831/maas/v1/tasks',data=b'{}',headers={'Content-Type':'application/json'}),401),
                ('query-string',urllib.request.Request('http://127.0.0.1:18831/maas/v1/tasks?x=1'),400),
                ('write-method',urllib.request.Request('http://127.0.0.1:18831/maas/v1/tasks',method='PUT'),403)):
                try:
                    with urllib.request.urlopen(req,timeout=3) as response:code=response.status
                except urllib.error.HTTPError as exc:code=exc.code
                denied[label]=code;assert code==expected
        image=Path('/root/wtaobj/service-validation/fixtures/upright.png').read_bytes()
        body={'model':'ocr.default','stream':False,'messages':[{'role':'user','content':[{'type':'image_url','image_url':{'url':'data:image/png;base64,'+base64.b64encode(image).decode()}}]}]}
        snapshot();code,value=request('/gpustack/v1/chat/completions',body);assert code==200
        report['sync_baseline']={'status':code,'lines':len(json.loads(value['choices'][0]['message']['content'])['lines'])};assert report['sync_baseline']['lines']==46
        code,artifact=request('/maas/v1/artifacts',image,'image/png');assert code==201
        task_body={'model':'ocr.default','input':{'image':{'artifact_id':artifact['id']}},'timeout_seconds':600 if options.node_maintenance else 180}
        code,task=request('/maas/v1/tasks',task_body,idem='baseline');assert code==202
        if options.operations:
            reserve="from maas_platform.config import Settings;from maas_platform.auth import AuthConnector;from maas_platform.service import TaskService;from maas_platform.sync_ingress import SyncIngress;s=Settings.from_env();v=TaskService(s,AuthConnector(s));print(SyncIngress(v).begin(v.auth.for_task('maintenance','maintenance'),'ocr.default',60))"
            token=run(*args(),'/work/.venv/bin/python','-c',reserve).strip()
            code,value=request('/gpustack/v1/chat/completions',body);assert code==429 and value['error']['code']=='SYNC_CAPACITY'
            assert not execute()
            release="from maas_platform.config import Settings;from maas_platform.auth import AuthConnector;from maas_platform.service import TaskService;from maas_platform.sync_ingress import SyncIngress;s=Settings.from_env();v=TaskService(s,AuthConnector(s));SyncIngress(v).finish("+repr(token)+",True)"
            run(*args(),'/work/.venv/bin/python','-c',release)
            report['unified_quota']={'sync_status':code,'async_waited':True,'reservation_only_no_backend_send':True}
        snapshot();assert execute();_,done=request('/maas/v1/tasks/'+task['id']);assert done['state']=='SUCCEEDED'
        cli('pause','--reason','P3 controlled ingress acceptance')
        report['paused']=cli('drain-status');assert report['paused']['managed_requests_drained'] and not report['paused']['safe_to_poweroff']
        code,value=request('/gpustack/v1/chat/completions',body);report['sync_paused']={'status':code,'code':value['error']['code']};assert code==503
        code,queued=request('/maas/v1/tasks',task_body,idem='queued-during-pause');assert code==202 and not execute()
        _,row=request('/maas/v1/tasks/'+queued['id']);assert row['state']=='QUEUED'
        code,value=request('/maas/v1/artifacts/'+done['result_artifact_id']);assert code==200
        report['stored_result_during_pause']={'status':code,'lines':len(value['outputs']['lines'])}
        try:cli('resume')
        except subprocess.CalledProcessError:pass
        else:raise RuntimeError('Unverified resume unexpectedly passed')
        assert cli('drain-status')['paused'];report['unverified_resume_rejected']=True
        if options.node_maintenance:
            operator=PRIVATE/'operator.json'
            journal=WORK/('node-session-'+uuid.uuid4().hex[:8]+'.json')
            node_ssh=['ssh','-F','/dev/null','-i','/root/wtaobj/maas-maintenance-access/id_ed25519',
                '-o','IdentitiesOnly=yes','-o','BatchMode=yes','-o','StrictHostKeyChecking=yes',
                '-o','UserKnownHostsFile=/root/wtaobj/maas-maintenance-access/known_hosts','-o','ConnectTimeout=5',
                '-o','ClearAllForwardings=yes','-o','ForwardAgent=no','-o','RequestTTY=no','root@10.11.31.2']
            write(operator,json.dumps({'journal':str(journal),'worker_id':2,'model_ids':[1,9],'credential_id':'maintenance','probe_image':'/probe.png',
                'task_cli':args()+['/work/.venv/bin/python','-m','maas_platform.cli'],'node_ssh':node_ssh,'wait_seconds':180}))
            run('systemd-run','--quiet','--unit',watchdog,'--on-active=480s','/usr/bin/python3',str(ROOT/'scripts/maas/runtime/maintenance_control.py'),'--config',str(operator),'recover')
            report['node_begin']=control('begin')
            report['node_stopped']=control('stop')
            assert report['node_stopped']['stop_evidence']['backend_stop_observed']
            time.sleep(10)
            proof=json.loads(run(*node_ssh,'status'));assert proof['backend_stop_observed']
            report['node_stopped_hold_10s']=proof
            code,value=request('/gpustack/v1/chat/completions',body);assert code==503
            report['node_stop_sync_rejected']={'status':code,'code':value['error']['code']}
            native={**body,'model':pin['route_name']}
            try:
                with urllib.request.urlopen(urllib.request.Request('http://127.0.0.1:18080/v1/chat/completions',data=json.dumps(native).encode(),
                    headers={'Content-Type':'application/json','Authorization':'Bearer '+key}),timeout=5) as response:native_code=response.status
            except urllib.error.HTTPError as exc:native_code=exc.code
            except OSError:native_code='transport_unavailable'
            assert native_code!=200;report['native_inference_while_stopped']=native_code
            report['node_recovered']=control('recover');snapshot()
            final=control('resume');report['node_final']=final;report['resume']=final['recovery_probe']
            assert final['phase']=='RESUMED'
        else:
            snapshot();report['resume']=cli('resume','--credential-id','maintenance','--probe-image','/probe.png')
        assert not report['resume']['maintenance']['paused'] and report['resume']['probes'][0]['lines']==46
        snapshot();assert execute();_,row=request('/maas/v1/tasks/'+queued['id']);assert row['state']=='SUCCEEDED'
        report['queued_task_after_resume']={'state':row['state'],'attempts':row['attempts']}
        report['metrics']=cli('status');assert report['metrics']['successful_tasks']==2
        report['sync_activity_states']=json.loads(sql("SELECT json_object_agg(state,n) FROM (SELECT state,count(*) n FROM sync_activity GROUP BY state) q"))
        assert report['sync_activity_states']=={'COMPLETED':3 if options.operations else 2}
        report['maintenance_audit']=json.loads(sql("SELECT json_agg(action ORDER BY id) FROM maintenance_events"))
        assert report['maintenance_audit']==(['pause','pause','verified-resume'] if options.node_maintenance else ['pause','verified-resume'])
        if options.operations:
            verify_code="""import json,os,time
from maas_platform.config import Settings
from maas_platform.auth import AuthConnector
from maas_platform.service import TaskService
from maas_platform.observability import snapshot,alerts
s=Settings.from_env();v=TaskService(s,AuthConnector(s));who=v.auth.for_task('maintenance','maintenance')
a=v.upload(who,b'expired input','text/plain')
with v.db.transaction() as c:c.execute("UPDATE artifacts SET expires_at=now()-interval '1 second' WHERE id=%s",(a['id'],))
expired=v.cleanup();assert expired==1
orphan=v.store.put(b'uncommitted test file');old=time.time()-s.orphan_grace_seconds-10;os.utime(v.store.path(orphan['id']),(old,old))
fresh=v.store.put(b'fresh uncommitted file');removed=v.cleanup_orphans();assert removed==1 and v.store.path(fresh['id']).exists();v.store.remove(fresh['id'])
data=snapshot(v);assert sum(x['quantity'] for x in data['sync_usage'])==1 and sum(x['quantity'] for x in data['task_usage'])==2
assert not alerts(v,data)
print(json.dumps({'expired_removed':expired,'orphans_removed':removed,'fresh_retained':True,'metrics':data,'alerts':alerts(v,data)},default=str))
"""
            report['operations']=json.loads(run(*args(),'/work/.venv/bin/python','-c',verify_code))
            metrics=run(*args(),'/work/.venv/bin/python','-m','maas_platform.cli','metrics')
            write(WORK/'metrics.prom',metrics)
            report['metrics_cli_verified']='maas_successful_sync_requests_total 1' in metrics
            assert report['metrics_cli_verified']
        report['ok']=True
    finally:
        if operator is not None and operator.exists():
            try:
                session=control('status')
                if session['phase']!='RESUMED':
                    control('recover')
                    status=cli('drain-status')
                    if status['backend_activity'] or status['sync_activity']:raise RuntimeError('Unresolved activity requires inspection')
                    cfg=json.loads(operator.read_text());run(*cfg['node_ssh'],'disarm')
                subprocess.run(['systemctl','stop',watchdog+'.timer'],capture_output=True)
            except Exception:
                report['cleanup_deferred_for_recovery']=True
                write(WORK/'acceptance.json',json.dumps(report,indent=2)+'\n')
                raise RuntimeError('Recovery incomplete; private config/API/database preserved for operator recovery') from None
        subprocess.run(['docker','rm','-f',API],capture_output=True)
        report['resources_removed']=res.cleanup();report['cleanup_failures']=res.failures
        if created_db:run('docker','exec','wta-maas-p1-test-db','psql','-U','maas','-d','postgres','-c','DROP DATABASE '+DB)
        shutil.rmtree(ROOT/'maintenance-artifacts',ignore_errors=True);shutil.rmtree(PRIVATE)
        write(WORK/'acceptance.json',json.dumps(report,indent=2)+'\n')
    print(json.dumps(report))


if __name__=='__main__':main()
