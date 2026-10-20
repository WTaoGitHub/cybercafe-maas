"""001-only acceptance using an isolated DB/API and the retained P2 model.

No worker, Docker daemon, model, firewall or original ingress changes.
"""
import base64,json,os,shutil,socket,subprocess,sys,time,urllib.request,urllib.error
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
    if socket.gethostname()!='XZ-31-001':raise RuntimeError('Controller 001 only')
    if PRIVATE.exists():raise RuntimeError('Prior private acceptance state exists; inspect before reuse')
    probe=socket.socket();probe.bind(('127.0.0.1',18830));probe.close()
    PRIVATE.mkdir(mode=0o700,parents=True);WORK.mkdir(mode=0o700,exist_ok=True)
    report={'scope':'temporary managed ingress only','original_ingress_modified':False};res=Resources();created_db=False
    def snapshot():
        health=json.loads(Path('/root/wtaobj/maas-runtime/health.json').read_text());assert health['ok']
        for name in ('pin.json','preflight.json'):shutil.copyfile(Path('/root/wtaobj/maas-runtime')/name,WORK/name)
    def args():return ['docker','run','--rm','--network','host','--env-file',str(PRIVATE/'app.env'),'-v',str(ROOT)+':/work',
        '-v',str(WORK)+':/trusted:ro','-v','/root/wtaobj/service-validation/fixtures/upright.png:/probe.png:ro','-w','/work','python:3.11-slim']
    def cli(*command):return json.loads(run(*args(),'/work/.venv/bin/python','-m','maas_platform.cli',*command))
    def sql(query):return run('docker','exec','wta-maas-p1-test-db','psql','-U','maas','-d',DB,'-tAc',query).strip()
    def request(path,body=None,media='application/json',idem=None):
        raw=json.dumps(body).encode() if isinstance(body,dict) else body
        headers={'Authorization':'Bearer '+key,'Content-Type':media}
        if idem:headers['Idempotency-Key']=idem
        try:
            with urllib.request.urlopen(urllib.request.Request('http://127.0.0.1:18830'+path,data=raw,headers=headers),timeout=40) as response:
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
        image=Path('/root/wtaobj/service-validation/fixtures/upright.png').read_bytes()
        body={'model':'ocr.default','stream':False,'messages':[{'role':'user','content':[{'type':'image_url','image_url':{'url':'data:image/png;base64,'+base64.b64encode(image).decode()}}]}]}
        snapshot();code,value=request('/v1/chat/completions',body);assert code==200
        report['sync_baseline']={'status':code,'lines':len(json.loads(value['choices'][0]['message']['content'])['lines'])};assert report['sync_baseline']['lines']==46
        code,artifact=request('/maas/v1/artifacts',image,'image/png');assert code==201
        task_body={'model':'ocr.default','input':{'image':{'artifact_id':artifact['id']}},'timeout_seconds':180}
        code,task=request('/maas/v1/tasks',task_body,idem='baseline');assert code==202
        snapshot();assert execute();_,done=request('/maas/v1/tasks/'+task['id']);assert done['state']=='SUCCEEDED'
        cli('pause','--reason','P3 controlled ingress acceptance')
        report['paused']=cli('drain-status');assert report['paused']['managed_requests_drained'] and not report['paused']['safe_to_poweroff']
        code,value=request('/v1/chat/completions',body);report['sync_paused']={'status':code,'code':value['error']['code']};assert code==503
        code,queued=request('/maas/v1/tasks',task_body,idem='queued-during-pause');assert code==202 and not execute()
        _,row=request('/maas/v1/tasks/'+queued['id']);assert row['state']=='QUEUED'
        code,value=request('/maas/v1/artifacts/'+done['result_artifact_id']);assert code==200
        report['stored_result_during_pause']={'status':code,'lines':len(value['outputs']['lines'])}
        try:cli('resume')
        except subprocess.CalledProcessError:pass
        else:raise RuntimeError('Unverified resume unexpectedly passed')
        assert cli('drain-status')['paused'];report['unverified_resume_rejected']=True
        snapshot();report['resume']=cli('resume','--credential-id','maintenance','--probe-image','/probe.png')
        assert not report['resume']['maintenance']['paused'] and report['resume']['probes'][0]['lines']==46
        snapshot();assert execute();_,row=request('/maas/v1/tasks/'+queued['id']);assert row['state']=='SUCCEEDED'
        report['queued_task_after_resume']={'state':row['state'],'attempts':row['attempts']}
        report['metrics']=cli('status');assert report['metrics']['successful_tasks']==2
        report['sync_activity_states']=json.loads(sql("SELECT json_object_agg(state,n) FROM (SELECT state,count(*) n FROM sync_activity GROUP BY state) q"))
        assert report['sync_activity_states']=={'COMPLETED':2}
        report['maintenance_audit']=json.loads(sql("SELECT json_agg(action ORDER BY id) FROM maintenance_events"));assert report['maintenance_audit']==['pause','verified-resume']
        report['ok']=True
    finally:
        subprocess.run(['docker','rm','-f',API],capture_output=True)
        report['resources_removed']=res.cleanup();report['cleanup_failures']=res.failures
        if created_db:run('docker','exec','wta-maas-p1-test-db','psql','-U','maas','-d','postgres','-c','DROP DATABASE '+DB)
        shutil.rmtree(ROOT/'maintenance-artifacts',ignore_errors=True);shutil.rmtree(PRIVATE)
        write(WORK/'acceptance.json',json.dumps(report,indent=2)+'\n')
    print(json.dumps(report))


if __name__=='__main__':main()
