"""Real private E6 OCR acceptance; no model/worker mutation or request replay."""
import base64,json,os,subprocess,time,urllib.request,uuid
from pathlib import Path
LIVE=Path('/root/wtaobj/maas-live');ROOT=Path('/root/wtaobj/maas-framework')
def run(*a):return subprocess.check_output(a,text=True,stderr=subprocess.PIPE,timeout=120)
def main():
    report={'started_at':time.time(),'image':'cybercafe-maas-tasks:e6-r1'}
    key=(LIVE/'secrets/pilot-key').read_text().strip()
    def request(path,data=None,media='application/json',idem=None):
        headers={'Authorization':'Bearer '+key,'Content-Type':media}
        if idem:headers['Idempotency-Key']=idem
        if isinstance(data,dict):data=json.dumps(data).encode()
        with urllib.request.urlopen(urllib.request.Request('http://127.0.0.1:18831'+path,data=data,headers=headers),timeout=40) as r:return r.status,json.load(r)
    assert run('systemctl','show','maas-collector.service','-p','UnitFileState','--value').strip()=='masked'
    for name in ('pin.json','preflight.json','health.json','collector.json','inventory_ed25519','consumer'):
        assert not (Path('/root/wtaobj/maas-runtime')/name).exists()
    for name in ('api','executor','observer'):
        container='maas-tasks-'+name+'-1'
        mounts=json.loads(run('docker','inspect','--format','{{json .Mounts}}',container))
        assert all(m['Destination']!='/run/maas-trusted' for m in mounts)
    report['collector_disabled_and_assets_absent']=True
    image=(LIVE/'probes/upright.png').read_bytes()
    body={'model':'ocr.default','stream':False,'messages':[{'role':'user','content':[{'type':'image_url','image_url':{'url':'data:image/png;base64,'+base64.b64encode(image).decode()}}]}]}
    code,value=request('/gpustack/v1/chat/completions',body);assert code==200
    assert len(json.loads(value['choices'][0]['message']['content'])['lines'])==46
    report['sync_lines']=46
    code,art=request('/maas/v1/artifacts',image,'image/png');assert code==201
    idem='e6-'+uuid.uuid4().hex;payload={'model':'ocr.default','input':{'image':{'artifact_id':art['id']}},'timeout_seconds':120}
    code,task=request('/maas/v1/tasks',payload,idem=idem);assert code==202
    for _ in range(80):
        _,row=request('/maas/v1/tasks/'+task['id'])
        if row['state'] in ('SUCCEEDED','FAILED','CANCELLED'):break
        time.sleep(.5)
    assert row['state']=='SUCCEEDED'
    _,result=request('/maas/v1/artifacts/'+row['result_artifact_id']);assert len(result['outputs']['lines'])==46
    _,again=request('/maas/v1/tasks',payload,idem=idem);assert again['id']==task['id']
    report['async']={'id':task['id'],'state':row['state'],'lines':46,'idempotency_same_task':True}
    cli=['docker','exec','maas-tasks-api-1','python','-m','maas_platform.cli']
    status=json.loads(run(*cli,'status'));assert status['unknown_backend_activity']==status['unknown_sync_activity']==0
    report['status']=status
    # Recompute host monitor now, with no collector files/SSH inventory available.
    run('python3',str(ROOT/'scripts/maas/runtime/monitor_live.py'))
    monitor=json.loads((LIVE/'monitor/status.json').read_text());assert monitor['ok'] and not monitor['alerts']
    report['monitor']=monitor;report['ok']=True;report['finished_at']=time.time()
    fd=os.open(LIVE/'e6-acceptance.json',os.O_CREAT|os.O_TRUNC|os.O_WRONLY,0o600)
    with os.fdopen(fd,'w') as f:json.dump(report,f,indent=2)
    print(json.dumps(report))
if __name__=='__main__':main()
