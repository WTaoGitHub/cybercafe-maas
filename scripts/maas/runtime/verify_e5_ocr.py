"""One sync and one async OCR request through verified private TLS; never replay.

Run on 001 after explicit pilot activation. Leaves inputs/results and usage in
the existing MaaS database. No restart, cleanup, collector or load-test action.
"""
import base64,json,re,ssl,subprocess,time,urllib.request,uuid
from pathlib import Path
LIVE=Path('/root/wtaobj/maas-live')
OUT=Path('/root/wtaobj/maas-e5-ocr/acceptance.json')


def main():
    if OUT.exists():raise RuntimeError('Acceptance evidence exists; inspect before another run')
    key=(LIVE/'secrets/pilot-key').read_text().strip()
    context=ssl.create_default_context(cafile='/root/wtaobj/maas-private-tls/server.crt')
    opener=urllib.request.build_opener(urllib.request.ProxyHandler({}),urllib.request.HTTPSHandler(context=context))
    report={'state':'STARTED','tls_verified':True,'started_at':time.time(),'scope':'single sync and single async request; no automatic replay'}
    def checkpoint():
        with OUT.open('w') as f:json.dump(report,f,indent=2)
        OUT.chmod(0o600)
    def request(path,body=None,media='application/json',idem=None):
        headers={'Authorization':'Bearer '+key,'Content-Type':media}
        if idem:headers['Idempotency-Key']=idem
        data=json.dumps(body).encode() if isinstance(body,dict) else body
        with opener.open(urllib.request.Request('https://127.0.0.1:18832'+path,data=data,headers=headers),timeout=40) as r:
            return r.status,json.load(r)
    checkpoint()
    try:
        image=(LIVE/'probes/upright.png').read_bytes()
        body={'model':'ocr.default','stream':False,'messages':[{'role':'user','content':[
            {'type':'image_url','image_url':{'url':'data:image/png;base64,'+base64.b64encode(image).decode()}}]}]}
        code,envelope=request('/v1/chat/completions',body)
        value=json.loads(envelope['choices'][0]['message']['content'])
        assert code==200 and len(value['lines'])==46
        report['sync']={'http':code,'lines':len(value['lines']),'node':value['node']};checkpoint()
        code,artifact=request('/maas/v1/artifacts',image,'image/png');assert code==201
        report['input_artifact_id']=artifact['id'];checkpoint()
        idem='e5-ocr-acceptance-'+uuid.uuid4().hex
        code,task=request('/maas/v1/tasks',{'model':'ocr.default','input':{'image':{'artifact_id':artifact['id']}},'timeout_seconds':120},idem=idem)
        assert code==202
        report['task']={'id':task['id'],'submit_http':code,'idempotency_key':idem};checkpoint()
        deadline=time.monotonic()+150
        while True:
            code,row=request('/maas/v1/tasks/'+task['id']);assert code==200
            if row['state'] in ('SUCCEEDED','FAILED','CANCELLED','UNKNOWN'):break
            if time.monotonic()>deadline:raise RuntimeError('TASK_POLL_TIMEOUT')
            time.sleep(.5)
        assert row['state']=='SUCCEEDED',row.get('error_code')
        code,result=request('/maas/v1/artifacts/'+row['result_artifact_id'])
        assert code==200 and len(result['outputs']['lines'])==46 and result['model_revision']=='ocr-e5-r1'
        assert result['outputs']['node']==value['node']
        report['task'].update(state=row['state'],attempts=row['attempts'],result_artifact_id=row['result_artifact_id'],
            result_download_http=code,revision=result['model_revision'],lines=46,node=result['outputs']['node'])
        assert re.fullmatch(r'[A-Za-z0-9_-]+',task['id'])
        sql="SELECT json_build_object('succeeded',count(*) FILTER (WHERE state='SUCCEEDED'),'usage_events',(SELECT count(*) FROM task_usage_events WHERE task_id='"+task['id']+"')) FROM tasks WHERE id='"+task['id']+"'"
        raw=subprocess.check_output(['docker','exec','maas-tasks-db-1','psql','-h','127.0.0.1','-p','18835','-U','maas','-d','maas','-tAc',sql],text=True,stderr=subprocess.PIPE,timeout=10)
        report['database']=json.loads(raw);assert report['database']=={'succeeded':1,'usage_events':1}
        report['state']='PASSED'
    except Exception as e:
        report['state']='FAILED';report['error_type']=type(e).__name__
        raise RuntimeError('E5 acceptance failed; inspect saved phase, do not replay uncertain inference') from None
    finally:
        report['finished_at']=time.time();checkpoint()
    print(json.dumps(report))


if __name__=='__main__':main()
