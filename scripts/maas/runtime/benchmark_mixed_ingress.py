"""Bounded live sync/async quota competition; no retries or deployment changes.

One producer queues 24 asynchronous tasks while three clients each send twelve
synchronous requests. Only explicit SYNC_CAPACITY/429 is an expected rejection.
Other failures stop new submissions; accepted tasks are observed, never replayed.
This is a fixed-count validation, not a continuously supplied capacity result.
"""
import argparse,base64,hashlib,json,re,ssl,sys,threading,time,urllib.error,urllib.request
from pathlib import Path
from benchmark_executors import LIVE,FIXTURES,atomic,bounded_map,check_activity,command,sql
from maas_bench.contracts import integer,parallel_arg


def verify_counts(records,tasks,sync_rows,async_count=24,sync_count=36):
    accepted=[r for r in records if r['kind']=='sync' and r.get('http_status')==200]
    rejected=[r for r in records if r['kind']=='sync' and r.get('http_status')==429]
    async_records=[r for r in records if r['kind']=='async']
    if len(tasks)!=async_count or len(async_records)!=async_count:raise RuntimeError('Async reconciliation mismatch')
    if any(r['state']!='SUCCEEDED' or r['attempts']!=1 or r['events']!=1 for r in tasks):raise RuntimeError('Async result/event mismatch')
    if not accepted or not rejected:raise RuntimeError('Both sync acceptance and quota rejection must be observed')
    if len(sync_rows)!=len(accepted) or any(r['state']!='COMPLETED' or r['outcome']!='SUCCEEDED' or r['events']!=1 for r in sync_rows):raise RuntimeError('Sync window reconciliation mismatch; inspect other traffic')
    if len(accepted)+len(rejected)!=sync_count:raise RuntimeError('Sync client count mismatch')


def mixed_parameters(containers,sync_clients):
    integer(sync_clients,'sync_clients',maximum=15)
    executors=sum(x['service']=='executor' for x in containers);integer(executors,'executors')
    pairs={(x['global_limit'],x['project_limit']) for x in containers}
    if len(pairs)!=1:raise RuntimeError('Runtime limits disagree')
    global_limit,project_limit=next(iter(pairs))
    integer(global_limit,'global_limit');integer(project_limit,'project_limit')
    if project_limit>global_limit:raise RuntimeError('Inconsistent runtime limits')
    return executors,global_limit,project_limit


def check_mixed_activity(value,global_limit,project_limit,generation):
    check_activity(value,global_limit,generation)
    if value['project_running']+value['project_sync_running']>project_limit:
        raise RuntimeError('Project shared quota exceeded')


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--run-id',required=True)
    parser.add_argument('--sync-clients',type=parallel_arg,default=3)
    parser.add_argument('--async-count',type=int,default=24);parser.add_argument('--sync-per-client',type=int,default=12)
    args=parser.parse_args()
    if not re.fullmatch(r'[a-z0-9-]{1,60}',args.run_id):parser.error('Bounded run ID required')
    if args.sync_clients>15 or not 1<=args.async_count<=200 or not 1<=args.sync_per_client<=200:parser.error('Bounded clients/counts required')
    root=Path('/root/wtaobj/maas-framework')
    pool=json.loads(command(['python3',str(root/'scripts/maas/runtime/executor_pool.py'),'status']))
    executors,global_limit,project_limit=mixed_parameters(pool['containers'],args.sync_clients)
    if pool['managed']['paused'] or pool['managed']['active_tasks'] or pool['managed']['backend_activity'] or pool['managed']['sync_activity']:raise RuntimeError('Exclusive idle test window required')
    folder=LIVE/'e3'/args.run_id;folder.mkdir(mode=0o700,exist_ok=False)
    before=sql("SELECT coalesce(json_agg(token),'[]'::json) FROM sync_activity")
    fixture=json.loads((FIXTURES/'manifest.json').read_text())[0]
    raw=(FIXTURES/fixture['file']).read_bytes()
    if hashlib.sha256(raw).hexdigest()!=fixture['sha256']:raise RuntimeError('Fixture changed')
    ctx=ssl.create_default_context(cafile='/root/wtaobj/maas-private-tls/server.crt')
    opener=urllib.request.build_opener(urllib.request.ProxyHandler({}),urllib.request.HTTPSHandler(context=ctx))
    key=(LIVE/'secrets/pilot-key').read_text().strip()
    stop=threading.Event();sampling_done=threading.Event();lock=threading.RLock();barrier=threading.Barrier(args.sync_clients+1)
    binding=json.loads((LIVE/'secrets/bindings.json').read_text())
    project=next(b['project'] for b in binding['credentials'] if b['id']=='pilot-ocr')
    project_sql="'"+project.replace("'","''")+"'"
    records=[];summary={'run_id':args.run_id,'state':'PREPARED','started_at':time.time(),'generation':pool['managed']['generation'],
        'executors':executors,'global_limit':global_limit,'project_limit':project_limit,
        'sync_clients':args.sync_clients,'async_count':args.async_count,'sync_attempts':args.sync_clients*args.sync_per_client,
        'tls_verified':True,'records':records,'sample_errors':[],
        'attribution':'Async by unique idempotency namespace; sync by token difference in an idle test window, not a persisted request-ID mapping.'}
    def checkpoint():
        with lock:atomic(folder/'status.json',summary)
    def request(path,body=None,media='application/json',idem=None,allow_capacity=False):
        headers={'Authorization':'Bearer '+key,'Content-Type':media}
        if idem:headers['Idempotency-Key']=idem
        payload=json.dumps(body).encode() if isinstance(body,dict) else body
        try:
            with opener.open(urllib.request.Request('https://127.0.0.1:18832'+path,data=payload,headers=headers),timeout=35) as r:return r.status,json.load(r),r.headers.get('X-Request-ID')
        except urllib.error.HTTPError as exc:
            if allow_capacity and exc.code==429:
                result=json.load(exc)
                if result.get('error',{}).get('code')=='SYNC_CAPACITY':return 429,result,exc.headers.get('X-Request-ID')
            raise
    code,artifact,_=request('/maas/v1/artifacts',raw,fixture['media_type']);assert code==201
    code,capability,_=request('/maas/v1/models/ocr.default/capabilities');assert code==200
    revision=capability['revision'];summary['model_revision']=revision
    def remember(kind,index):
        if stop.is_set():raise RuntimeError('Mixed run stopped; no new submission')
        record={'kind':kind,'index':index,'state':'SENDING','started_at':time.time()}
        with lock:records.append(record);checkpoint()
        return record
    def observe(record):
        deadline=time.monotonic()+145
        while True:
            code,row,_=request('/maas/v1/tasks/'+record['task']);assert code==200
            if row['state'] in ('SUCCEEDED','FAILED','CANCELLED'):break
            if time.monotonic()>deadline:raise RuntimeError('Accepted task observation deadline')
            time.sleep(.05)
        assert row['state']=='SUCCEEDED' and row['attempts']==1
        code,result,_=request('/maas/v1/artifacts/'+row['result_artifact_id']);assert code==200
        assert result['model_revision']==revision and isinstance(result['outputs']['lines'],list)
        with lock:record.update(state='SUCCEEDED',lines=len(result['outputs']['lines']),finished_at=time.time());checkpoint()
    def lane(number):
        barrier.wait(timeout=10)
        try:
            if number==0:
                pending=[]
                for i in range(args.async_count):
                    record=remember('async',i)
                    code,row,rid=request('/maas/v1/tasks',{'model':'ocr.default','input':{'image':{'artifact_id':artifact['id']}},'timeout_seconds':120,'max_attempts':1},idem='e3-'+args.run_id+'-'+str(i))
                    assert code==202
                    with lock:record.update(task=row['id'],request_id=rid);checkpoint()
                    pending.append(record)
                for record in pending:observe(record)
            else:
                for i in range(args.sync_per_client):
                    record=remember('sync',(number-1)*args.sync_per_client+i)
                    code,row,rid=request('/ocr',{'model':'ocr.default','image_base64':base64.b64encode(raw).decode()},allow_capacity=True)
                    if code==200:assert isinstance(row['lines'],list)
                    with lock:record.update(state='SUCCEEDED' if code==200 else 'CAPACITY_REJECTED',http_status=code,request_id=rid,finished_at=time.time());checkpoint()
        except Exception:
            stop.set();raise
    def sampler():
        while not sampling_done.is_set():
            try:
                value=sql("SELECT json_build_object('running',(SELECT count(*) FROM tasks WHERE state IN ('RUNNING','CANCEL_REQUESTED')),'sync_running',(SELECT count(*) FROM sync_activity WHERE state='DISPATCHING'),'unknown',(SELECT count(*) FROM backend_activity WHERE state='UNKNOWN'),'sync_unknown',(SELECT count(*) FROM sync_activity WHERE state='UNKNOWN'),'paused',(SELECT paused FROM maintenance WHERE id=1),'generation',(SELECT generation FROM maintenance WHERE id=1),'project_running',(SELECT count(*) FROM tasks WHERE state IN ('RUNNING','CANCEL_REQUESTED') AND project="+project_sql+"),'project_sync_running',(SELECT count(*) FROM sync_activity WHERE state='DISPATCHING' AND project="+project_sql+"))")
                check_mixed_activity(value,global_limit,project_limit,summary['generation'])
                sample={'at':time.time(),**value}
                with (folder/'samples.jsonl').open('a') as f:f.write(json.dumps(sample)+'\n')
            except Exception as exc:
                stop.set()
                with lock:summary['sample_errors'].append({'error_class':type(exc).__name__});checkpoint()
                return
            sampling_done.wait(.1)
    thread=threading.Thread(target=sampler);summary['state']='RUNNING';checkpoint();thread.start()
    try:
        bounded_map(lane,range(args.sync_clients+1),args.sync_clients+1,stop)
        if stop.is_set():raise RuntimeError('Mixed run guard stopped dispatch')
        summary['state']='LOAD_FINISHED'
    except Exception as exc:
        summary.update(state='INTERRUPTED_REQUIRES_INSPECTION',error_class=type(exc).__name__)
    finally:
        sampling_done.set();thread.join(timeout=25)
        try:
            summary['tasks']=sql("SELECT coalesce(json_agg(json_build_object('id',t.id,'state',t.state,'attempts',t.attempts,'events',(SELECT count(*) FROM task_usage_events u WHERE u.task_id=t.id))),'[]'::json) FROM tasks t WHERE idempotency_key LIKE 'e3-"+args.run_id+"-%'")
            all_sync=sql("SELECT coalesce(json_agg(json_build_object('token',s.token,'state',s.state,'outcome',s.outcome,'events',(SELECT count(*) FROM sync_usage_events u WHERE u.token=s.token))),'[]'::json) FROM sync_activity s WHERE NOT s.is_probe")
            summary['sync_rows']=[r for r in all_sync if r['token'] not in before]
            samples=[json.loads(x) for x in (folder/'samples.jsonl').read_text().splitlines()]
            summary['sample_count']=len(samples);summary['mixed_overlap_samples']=sum(r['running']>0 and r['sync_running']>0 for r in samples)
            summary['max_combined_inflight']=max((r['running']+r['sync_running'] for r in samples),default=0)
            if summary['state']=='LOAD_FINISHED' and not summary['sample_errors']:
                verify_counts(records,summary['tasks'],summary['sync_rows'],args.async_count,args.sync_clients*args.sync_per_client)
                if not summary['mixed_overlap_samples']:raise RuntimeError('No actual sync/async overlap observed')
                summary['state']='COMPLETE'
        except Exception as exc:
            summary.update(state='INTERRUPTED_REQUIRES_INSPECTION',verification_error_class=type(exc).__name__)
        summary['finished_at']=time.time();checkpoint()
    print(json.dumps({k:v for k,v in summary.items() if k not in ('records','tasks','sync_rows')}))
    if summary['state']!='COMPLETE':raise RuntimeError('Mixed validation incomplete; inspect accepted IDs without replay')


if __name__=='__main__':main()
