"""001 persistent OCR acceptance: real non-root image, shared data and safe restart.
Leaves successful tasks/results in the live pilot DB. Does not destroy volumes.
"""
import base64,json,os,subprocess,time,urllib.request,urllib.error,uuid
from pathlib import Path
ROOT=Path('/root/wtaobj/maas-framework');LIVE=Path('/root/wtaobj/maas-live')
COMPOSE=['docker','compose','--env-file',str(LIVE/'app.env'),'-f',str(ROOT/'deploy/maas/compose.yaml')]


def run(*args):return subprocess.check_output(args,text=True,stderr=subprocess.PIPE,timeout=120)
def cli(*args):return json.loads(run(*COMPOSE,'exec','-T','api','python','-m','maas_platform.cli',*args))
def inspect():
    ids=run(*COMPOSE,'ps','-aq').split()
    rows=json.loads(run('docker','inspect',*ids))
    return {x['Config']['Labels']['com.docker.compose.service']:{'id':x['Id'],'started':x['State']['StartedAt'],
        'running':x['State']['Running'],'restart':x['HostConfig']['RestartPolicy']['Name'],
        'volumes':sorted([{'destination':v['Destination'],'name':v.get('Name'),'rw':v['RW']} for v in x['Mounts']],key=lambda v:v['destination'])} for x in rows}


def main():
    key=(LIVE/'secrets/pilot-key').read_text().strip()
    def request(path,body=None,media='application/json',idem=None,direct=False):
        headers={'Authorization':'Bearer '+key,'Content-Type':media}
        if idem:headers['Idempotency-Key']=idem
        data=json.dumps(body).encode() if isinstance(body,dict) else body
        try:
            with urllib.request.urlopen(urllib.request.Request('http://127.0.0.1:'+('18830' if direct else '18831')+path,data=data,headers=headers),timeout=40) as r:
                return r.status,json.load(r)
        except urllib.error.HTTPError as e:return e.code,json.load(e)
    def ready():
        deadline=time.monotonic()+120
        while time.monotonic()<deadline:
            try:
                if request('/healthz',direct=True)[0]==200:return
            except (OSError,ValueError):pass
            time.sleep(1)
        raise RuntimeError('API readiness timeout')
    def wait_task(ident):
        deadline=time.monotonic()+120
        while time.monotonic()<deadline:
            code,row=request('/maas/v1/tasks/'+ident);assert code==200
            if row['state'] in ('SUCCEEDED','FAILED','CANCELLED'):
                assert row['state']=='SUCCEEDED',row['state'];return row
            time.sleep(.5)
        raise RuntimeError('Task completion timeout')
    ready();report={'started_at':time.time(),'through_nginx':True,'scope':'persistent private OCR pilot; no host or Docker daemon restart'}
    report['compose_version']=run('docker','compose','version','--short').strip()
    image=(LIVE/'probes/upright.png').read_bytes()
    body={'model':'ocr.default','stream':False,'messages':[{'role':'user','content':[{'type':'image_url','image_url':{'url':'data:image/png;base64,'+base64.b64encode(image).decode()}}]}]}
    code,value=request('/gpustack/v1/chat/completions',body);assert code==200
    assert len(json.loads(value['choices'][0]['message']['content'])['lines'])==46
    report['sync']={'status':code,'lines':46}
    code,art=request('/maas/v1/artifacts',image,'image/png');assert code==201
    taskbody={'model':'ocr.default','input':{'image':{'artifact_id':art['id']}},'timeout_seconds':600}
    code,first=request('/maas/v1/tasks',taskbody,idem='persistent-before-'+uuid.uuid4().hex);assert code==202
    first=wait_task(first['id']);code,result=request('/maas/v1/artifacts/'+first['result_artifact_id']);assert code==200 and len(result['outputs']['lines'])==46
    report['first_task']={'id':first['id'],'state':first['state'],'lines':46}
    uidcheck="""import json,os,time
from pathlib import Path
assert os.getuid()==10001
assert not Path('/run/maas-trusted').exists()
assert not Path('/var/run/docker.sock').exists()
assert not Path('/run/secrets/collector.json').exists()
print(json.dumps({'uid':os.getuid(),'collector_mount_absent':True,'docker_socket_absent':True}))
"""
    report['consumer']=json.loads(run(*COMPOSE,'exec','-T','api','python','-c',uidcheck))
    cli('pause','--reason','P4 persistent service restart acceptance')
    status=cli('drain-status');assert status['managed_requests_drained']
    code,queued=request('/maas/v1/tasks',taskbody,idem='persistent-restart-'+uuid.uuid4().hex);assert code==202 and queued['state']=='QUEUED'
    before=inspect();run(*COMPOSE,'restart','--timeout','30');ready();after=inspect()
    assert set(before)==set(after)=={'api','executor','observer','db'}
    for name in before:
        assert before[name]['id']==after[name]['id'] and before[name]['started']!=after[name]['started']
        assert after[name]['running'] and after[name]['restart']=='unless-stopped'
        assert before[name]['volumes']==after[name]['volumes']
    report['restart']={'services':list(after),'same_containers_and_volumes':True,'containers_restarted':True,'host_restarted':False,'docker_daemon_restarted':False}
    assert cli('drain-status')['paused']
    code,row=request('/maas/v1/tasks/'+queued['id']);assert code==200 and row['state']=='QUEUED'
    code,value=request('/maas/v1/artifacts/'+first['result_artifact_id']);assert code==200 and len(value['outputs']['lines'])==46
    report['persisted']={'queued_retained':True,'maintenance_pause_retained':True,'old_result_readable':True}
    resumed=cli('resume','--credential-id','pilot-ocr','--probe-image','/run/maas-probe/upright.png');assert not resumed['maintenance']['paused']
    second=wait_task(queued['id']);report['queued_after_restart']={'id':second['id'],'state':second['state'],'attempts':second['attempts']}
    report['status']=cli('status');assert report['status']['unknown_backend_activity']==report['status']['unknown_sync_activity']==0
    observer="""import json,time
from pathlib import Path
p=Path('/var/lib/maas/artifacts/.observability/status.json');d=json.loads(p.read_text());assert d['ok'] and time.time()-d['generated_at_unix']<90
print(json.dumps({'ok':d['ok'],'fresh':True,'codes':[a['code'] for a in d['alerts']]}))
"""
    # Observer may have sampled the intended maintenance pause. Refresh once now,
    # then let its resident 60s loop continue independently.
    run(*COMPOSE,'exec','-T','observer','python','-m','maas_platform.observe','--output','/var/lib/maas/artifacts/.observability')
    report['observer']=json.loads(run(*COMPOSE,'exec','-T','observer','python','-c',observer));assert not report['observer']['codes']
    report['finished_at']=time.time();report['ok']=True
    path=LIVE/'acceptance.json';fd=os.open(path,os.O_CREAT|os.O_TRUNC|os.O_WRONLY,0o600)
    with os.fdopen(fd,'w') as f:json.dump(report,f,indent=2)
    print(json.dumps(report))


if __name__=='__main__':main()
