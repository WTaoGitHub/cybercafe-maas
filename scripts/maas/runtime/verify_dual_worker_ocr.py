"""Bounded real two-worker routing + async OCR acceptance on 001.

Never changes deployments, invokes bootstrap/apply, pulls images, or retries a
failed send. Each sync call is a distinct intentional probe (maximum 14).
Responses may legitimately come from DIFFERENT GPUs after scaling out.
"""
import argparse
import base64
import json
import os
from pathlib import Path
import ssl
import subprocess
import sys
import time
import urllib.request
import uuid

sys.path.insert(0, '/root/wtaobj')
LIVE = Path('/root/wtaobj/maas-live')


def check(value, message):
    if not value:
        raise RuntimeError(message)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out', required=True, type=Path)
    args = parser.parse_args()
    check(not args.out.exists(), 'Report exists; do not replay an earlier run')
    from gpustack_admin import api
    instances = [i for i in api('/v2/model-instances')['items'] if i['model_id'] == 18]
    workers = [w for w in api('/v2/workers')['items'] if w['id'] in (7, 8)]
    check(len(instances) == 14 and all(i['state'] == 'running' for i in instances), 'Expected 14 running')
    check(len(workers) == 2 and all(w['state'] == 'ready' and not w['unreachable'] for w in workers), 'Workers not ready')
    node_map = {w['status']['gpu_devices'][0]['uuid']: w['id'] for w in workers}
    check(len(node_map) == 2, 'Expected two distinct GPU identities')
    key = (LIVE/'secrets/pilot-key').read_text().strip()
    context = ssl.create_default_context(cafile='/root/wtaobj/maas-private-tls/server.crt')
    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, req, fp, code, msg, headers, newurl): return None
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect(),
                                         urllib.request.HTTPSHandler(context=context))
    report = {'state':'STARTED', 'started_at':time.time(), 'step':'precheck',
              'sync_probes':[], 'requests':[], 'known_task_id':None,
              'no_automatic_retries':True, 'tls_verified':True, 'deployment_changes':False}
    args.out.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    def save():
        tmp=args.out.with_suffix('.tmp')
        with tmp.open('w') as f:
            os.fchmod(f.fileno(),0o600)
            json.dump(report,f,indent=2);f.flush();os.fsync(f.fileno())
        os.replace(tmp,args.out)
    def request(method,path,body=None,raw=None,media='application/json',idem=None):
        headers={'Authorization':'Bearer '+key,'Content-Type':media}
        if idem:headers['Idempotency-Key']=idem
        if body is not None:raw=json.dumps(body).encode()
        event={'method':method,'path':path,'state':'SENDING'};report['requests'].append(event);save()
        with opener.open(urllib.request.Request('https://127.0.0.1:18832'+path,
                           data=raw,headers=headers,method=method),timeout=40) as r:
            value=json.load(r)
            event.update(state='RECEIVED',status=r.status,request_id=r.headers.get('X-Request-ID'));save()
            return r.status,value
    save()
    try:
        monitor=json.loads((LIVE/'monitor/status.json').read_text())
        check(monitor['metrics']['maas_unknown_backend_activity']==0 and monitor['metrics']['maas_unknown_sync_activity']==0,
              'Unresolved UNKNOWN activity')
        raw=(LIVE/'probes/upright.png').read_bytes()
        body={'model':'ocr.default','stream':False,'messages':[{'role':'user','content':[
            {'type':'image_url','image_url':{'url':'data:image/png;base64,'+base64.b64encode(raw).decode()}}]}]}
        seen=set();report['step']='bounded-distinct-gateway-probes'
        for n in range(14):
            code,value=request('POST','/v1/chat/completions',body=body)
            result=json.loads(value['choices'][0]['message']['content'])
            check(code==200 and len(result['lines'])==46,'Unexpected sync OCR output')
            node=result['node'];check(node in node_map,'Response came from unexpected GPU')
            seen.add(node_map[node])
            report['sync_probes'].append({'probe':n+1,'worker_id':node_map[node],'node':node,'lines':46})
            report['gateway_workers_observed']=sorted(seen);save()
            if seen=={7,8}:break
        check(seen=={7,8},'Bounded gateway sample did not exercise both workers; no automatic retries')
        report['step']='async-upload'
        code,artifact=request('POST','/maas/v1/artifacts',raw=raw,media='image/png')
        check(code==201,'Upload not created');report['input_artifact_id']=artifact['id'];save()
        report['step']='async-submit';report['idempotency_key']='dual-worker-'+uuid.uuid4().hex;save()
        code,task=request('POST','/maas/v1/tasks',body={
            'model':'ocr.default','input':{'image':{'artifact_id':artifact['id']}},
            'parameters':{},'timeout_seconds':120,'max_attempts':1},idem=report['idempotency_key'])
        check(code==202,'Task not newly accepted');report['known_task_id']=task['id'];save()
        deadline=time.monotonic()+150
        report['step']='async-poll'
        while True:
            _,row=request('GET','/maas/v1/tasks/'+task['id'])
            if row['state'] in ('SUCCEEDED','FAILED','CANCELLED'):break
            check(time.monotonic()<deadline,'Task still active; do not resubmit')
            time.sleep(1)
        report['task_state']=row['state'];report['task_error_code']=row['error_code'];save()
        check(row['state']=='SUCCEEDED','Async task did not succeed')
        _,result=request('GET','/maas/v1/artifacts/'+row['result_artifact_id'])
        check(len(result['outputs']['lines'])==46 and result['model_revision']=='ocr-registry-r1','Unexpected async result')
        node=result['outputs']['node'];check(node in node_map,'Async result came from unexpected GPU')
        report['async']={'task_id':task['id'],'state':row['state'],'attempts':row['attempts'],
                         'worker_id':node_map[node],'node':node,'lines':46,
                         'revision':result['model_revision'],'result_artifact_id':row['result_artifact_id']}
        report['step']='unique-task-metering'
        sql="""import os,json,sys,psycopg
with psycopg.connect(os.environ['MAAS_DATABASE_URL']) as c:
 c.execute('SET TRANSACTION READ ONLY')
 row=c.execute('SELECT count(*) FROM task_usage_events WHERE task_id=%s',(sys.argv[1],)).fetchone()
 print(json.dumps({'task_success_events':row[0]}))
"""
        value=json.loads(subprocess.check_output(['docker','exec','maas-tasks-api-1','python','-c',sql,task['id']],text=True,stderr=subprocess.PIPE,timeout=15))
        check(value['task_success_events']==1,'Unexpected task usage count')
        report['database']=value;report['state']='PASSED';report['step']='complete'
    except Exception as exc:
        report['state']='FAILED';report['error_type']=type(exc).__name__
        raise RuntimeError('Acceptance failed; inspect checkpoint and known task before another run') from None
    finally:
        report['finished_at']=time.time();save()
    print(json.dumps(report))


if __name__=='__main__':main()
