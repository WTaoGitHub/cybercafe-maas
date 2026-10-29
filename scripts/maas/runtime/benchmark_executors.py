"""Bounded live OCR executor comparison; verified TLS, unique IDs, no inference retries.
Keeps native replicas/claim unchanged. Uses native worker metrics, not SSH inventory.
This fixed-count legacy comparison does not establish continuously supplied saturation.
"""
import argparse,concurrent.futures,hashlib,json,math,os,re,ssl,subprocess,sys,threading,time,urllib.request
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[3]))
from maas_bench.contracts import integer,parallel_arg
LIVE=Path('/root/wtaobj/maas-live');FIXTURES=Path('/root/wtaobj/maas-endurance-fixtures')


def command(args):return subprocess.check_output(args,text=True,stderr=subprocess.PIPE,timeout=20)
def sql(query):return json.loads(command(['docker','exec','maas-tasks-db-1','psql','-h','127.0.0.1','-p','18835','-U','maas','-d','maas','-tAc',query]))
def percentile(values,q):
    a=sorted(values);return a[min(len(a)-1,int((len(a)-1)*q))] if a else None


def check_activity(value,limit,generation):
    """Stop dispatch on quarantine, quota violation or a changed maintenance owner."""
    if value['unknown'] or value['sync_unknown']:raise RuntimeError('UNKNOWN activity; stop new load')
    if value['running']+value['sync_running']>limit:raise RuntimeError('Shared quota exceeded')
    if value['paused'] or value['generation']!=generation:raise RuntimeError('Maintenance changed during benchmark')


def worker_metrics(lines,names):
    """Read the validated single-GPU workers; absent optional gauges remain unknown."""
    optional={'worker_node_gram_allocated_bytes','worker_node_gpu_temperature_celsius'}
    values={}
    for name in names:
        pattern=re.compile(r'^gpustack:'+re.escape(name)+r'(?:\{.*\})?\s+(\S+)(?:\s+\d+)?\s*$')
        for line in lines:
            match=pattern.match(line)
            if match:
                value=float(match.group(1))
                if not math.isfinite(value):raise ValueError('Non-finite native metric')
                values[name]=value;break
        if name not in values and name not in optional:
            raise ValueError('Required native worker metric is missing')
    return {name:values.get(name) for name in names}


def bounded_map(function,items,workers,stop=None):
    """Keep at most workers submitted calls; drain in-flight work after first failure."""
    integer(workers,'clients')
    stop=stop if stop is not None else threading.Event()
    iterator=iter(enumerate(items));results={};pending={};failure=None
    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:
        def fill():
            while len(pending)<workers and not stop.is_set():
                try:index,item=next(iterator)
                except StopIteration:return
                pending[pool.submit(function,item)]=index
        fill()
        while pending:
            done,_=concurrent.futures.wait(pending,return_when=concurrent.futures.FIRST_COMPLETED)
            for future in done:
                index=pending.pop(future)
                try:results[index]=future.result()
                except BaseException as exc:
                    stop.set()
                    if failure is None:failure=exc
            if failure is None:fill()
        if failure is not None:raise failure
    if stop.is_set():raise RuntimeError('Concurrent run halted; no further dispatch')
    return [results[i] for i in sorted(results)]

def atomic(path,value):
    temp=path.with_suffix('.new');fd=os.open(temp,os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600)
    with os.fdopen(fd,'w') as f:json.dump(value,f,indent=2);f.flush();os.fsync(f.fileno())
    os.replace(temp,path)


def main():
    p=argparse.ArgumentParser();p.add_argument('--run-id',required=True);p.add_argument('--executors',type=parallel_arg,required=True);p.add_argument('--clients',type=parallel_arg);p.add_argument('--model-id',type=int,default=18);p.add_argument('--model-replicas',type=parallel_arg);p.add_argument('--logical-model',default='ocr.default');p.add_argument('--count',type=int,default=40);a=p.parse_args()
    a.clients=a.clients or a.executors
    if a.model_id<=0 or not re.fullmatch(r'[A-Za-z0-9_.-]{1,128}',a.logical_model):p.error('Valid model identity required')
    if not re.fullmatch(r'[a-z0-9-]{1,60}',a.run_id) or not 4<=a.count<=1000:raise ValueError('Bounded run ID/count required')
    root=LIVE/'e3';root.mkdir(mode=0o700,exist_ok=True);folder=root/a.run_id;folder.mkdir(mode=0o700,exist_ok=False)
    pool=json.loads(command(['python3','/root/wtaobj/maas-framework/scripts/maas/runtime/executor_pool.py','status']))
    assert sum(r['service']=='executor' for r in pool['containers'])==a.executors
    assert not pool['managed']['paused'] and not pool['managed']['backend_activity'] and not pool['managed']['sync_activity']
    sys.path[:0]=[str(Path(__file__).resolve().parents[3]),'/root/wtaobj'];from gpustack_admin import api
    model_path='/v2/models/'+str(a.model_id);model=api(model_path);instances=api(model_path+'/instances')['items']
    integer(model['replicas'],'runtime replicas')
    if a.model_replicas is not None and model['replicas']!=a.model_replicas:raise RuntimeError('Runtime replica count differs from candidate')
    assert len(instances)==model['replicas'] and all(i['state']=='running' for i in instances)
    workers={i:api('/v2/workers/'+str(i)) for i in {row['worker_id'] for row in instances}}
    assert all(w['state']=='ready' for w in workers.values())
    from maas_platform.cluster_maintenance import model_hash
    baseline_hash=model_hash(model)
    ctx=ssl.create_default_context(cafile='/root/wtaobj/maas-private-tls/server.crt');key=(LIVE/'secrets/pilot-key').read_text().strip()
    def request(path,body=None,media='application/json',idem=None):
        headers={'Authorization':'Bearer '+key,'Content-Type':media}
        if idem:headers['Idempotency-Key']=idem
        raw=json.dumps(body).encode() if isinstance(body,dict) else body
        with urllib.request.urlopen(urllib.request.Request('https://127.0.0.1:18832'+path,data=raw,headers=headers),context=ctx,timeout=35) as r:return r.status,json.load(r)
    manifest=json.loads((FIXTURES/'manifest.json').read_text());inputs=[]
    code,capability=request('/maas/v1/models/'+a.logical_model+'/capabilities');assert code==200
    revision=capability['revision']
    for item in manifest:
        raw=(FIXTURES/item['file']).read_bytes();assert hashlib.sha256(raw).hexdigest()==item['sha256']
        code,art=request('/maas/v1/artifacts',raw,item['media_type']);assert code==201
        inputs.append({**item,'artifact':art['id']})
    lock=threading.Lock();stop=threading.Event();samples_stop=threading.Event();records=[]
    summary={'run_id':a.run_id,'executors':a.executors,'clients':a.clients,'count':a.count,'fixture_count':len(inputs),
             'fixture_manifest_sha256':hashlib.sha256((FIXTURES/'manifest.json').read_bytes()).hexdigest(),
             'model_id':a.model_id,'logical_model':a.logical_model,'model_revision':revision,'model_replicas':model['replicas'],'model_sha256':baseline_hash,'instances':[{'id':i['id'],'worker_id':i['worker_id']} for i in instances],
             'pool':pool,'tls_verified':True,'state':'RUNNING','started_at':time.time(),'records':records,'sample_errors':[]}
    def checkpoint():atomic(folder/'status.json',summary)
    def one(index,warmup=False):
        if stop.is_set():raise RuntimeError('Run halted; no new dispatch')
        item=inputs[index%len(inputs)];idem='e3-'+a.run_id+'-'+('warm-' if warmup else '')+str(index)
        record={'index':index,'warmup':warmup,'image':item['id'],'source':item['source'],'idempotency_key':idem,'intent_at':time.time()}
        with lock:records.append(record);checkpoint()
        start=time.monotonic()
        try:
            code,row=request('/maas/v1/tasks',{'model':a.logical_model,'input':{'image':{'artifact_id':item['artifact']}},'timeout_seconds':120,'max_attempts':1},idem=idem)
            assert code==202
            with lock:record['task']=row['id'];checkpoint()
            deadline=time.monotonic()+145
            while True:
                code,row=request('/maas/v1/tasks/'+record['task']);assert code==200
                if row['state'] in ('SUCCEEDED','FAILED','CANCELLED'):break
                if time.monotonic()>deadline:raise RuntimeError('Task deadline')
                time.sleep(.05)
            assert row['state']=='SUCCEEDED' and row['attempts']==1
            code,result=request('/maas/v1/artifacts/'+row['result_artifact_id']);assert code==200
            assert result['model_revision']==revision and isinstance(result['outputs']['lines'],list)
            with lock:record.update(state='SUCCEEDED',attempts=1,lines=len(result['outputs']['lines']),e2e_seconds=time.monotonic()-start,finished_at=time.time());checkpoint()
        except Exception as exc:
            stop.set()
            with lock:record.update(state='CLIENT_FAILED',error_class=type(exc).__name__);checkpoint()
            raise
    for i in range(2):one(i,True)
    def sampler():
        while not samples_stop.is_set():
            sample={'at':time.time(),'workers':{}}
            for ident,worker in workers.items():
                ip=worker['ip']
                try:
                    with urllib.request.urlopen('http://'+ip+':10151/metrics',timeout=2) as r:lines=r.read().decode().splitlines()
                    names=['worker_node_gpu_utilization_rate','worker_node_gram_used_bytes','worker_node_gram_allocated_bytes','worker_node_memory_used_bytes','worker_node_cpu_utilization_rate','worker_node_gpu_temperature_celsius']
                    sample['workers'][str(ident)]=worker_metrics(lines,names)
                    sample['workers'][str(ident)]['missing_optional_metrics']=[n for n in names if sample['workers'][str(ident)][n] is None]
                except Exception as exc:
                    stop.set();sample['workers'][str(ident)]={'error_class':type(exc).__name__}
                    with lock:summary['sample_errors'].append({'worker':ident,'error_class':type(exc).__name__})
            try:
                sample['tasks']=sql("SELECT json_build_object('running',(SELECT count(*) FROM tasks WHERE state IN ('RUNNING','CANCEL_REQUESTED')),'sync_running',(SELECT count(*) FROM sync_activity WHERE state='DISPATCHING'),'unknown',(SELECT count(*) FROM backend_activity WHERE state='UNKNOWN'),'sync_unknown',(SELECT count(*) FROM sync_activity WHERE state='UNKNOWN'),'paused',(SELECT paused FROM maintenance WHERE id=1),'generation',(SELECT generation FROM maintenance WHERE id=1),'lock_waiters',(SELECT count(*) FROM pg_stat_activity WHERE datname='maas' AND wait_event_type='Lock'))")
                check_activity(sample['tasks'],pool['containers'][0]['global_limit'],pool['managed']['generation'])
                mem={x.split(':')[0]:int(x.split()[1])*1024 for x in Path('/proc/meminfo').read_text().splitlines() if ':' in x}
                sample['controller_mem_available_bytes']=mem['MemAvailable'];sample['controller_loadavg']=Path('/proc/loadavg').read_text().split()[:3]
            except Exception as exc:
                stop.set();sample['sample_error_class']=type(exc).__name__
                with lock:summary['sample_errors'].append({'component':'activity','error_class':type(exc).__name__})
            with (folder/'samples.jsonl').open('a') as f:f.write(json.dumps(sample)+'\n')
            samples_stop.wait(1)
    sampler_thread=threading.Thread(target=sampler);sampler_thread.start();started=time.monotonic()
    try:
        bounded_map(one,range(a.count),a.clients,stop)
        elapsed=time.monotonic()-started;measured=[r for r in records if not r['warmup']]
        after=api(model_path);current=api(model_path+'/instances')['items']
        assert after['replicas']==model['replicas'] and model_hash(after)==baseline_hash and {i['id'] for i in current}=={i['id'] for i in instances}
        summary.update(state='COMPLETE',elapsed_seconds=elapsed,throughput_images_per_second=len(measured)/elapsed,
            e2e_p50_seconds=percentile([r['e2e_seconds'] for r in measured],.5),e2e_p95_seconds=percentile([r['e2e_seconds'] for r in measured],.95),e2e_p99_seconds=percentile([r['e2e_seconds'] for r in measured],.99))
    finally:
        stop.set();samples_stop.set();sampler_thread.join(timeout=8)
        # Namespace-limited reconciliation: no dependence on unrelated customer traffic totals.
        prefix='e3-'+a.run_id+'-%'
        summary['database']=sql("SELECT coalesce(json_agg(json_build_object('id',t.id,'state',t.state,'attempts',t.attempts,'created_at',t.created_at,'updated_at',t.updated_at,'success_events',(SELECT count(*) FROM task_usage_events u WHERE u.task_id=t.id),'execution_seconds',(SELECT sum(extract(epoch FROM ended_at-started_at)) FROM task_attempts a WHERE a.task_id=t.id))), '[]'::json) FROM tasks t WHERE t.idempotency_key LIKE '"+prefix+"'")
        summary['finished_at']=time.time()
        good=all(r['state']=='SUCCEEDED' and r['attempts']==1 and r['success_events']==1 for r in summary['database'])
        if len(summary['database'])!=a.count+2 or not good or summary['state']!='COMPLETE' or summary['sample_errors']:summary['state']='FAILED'
        checkpoint()
    print(json.dumps({k:v for k,v in summary.items() if k not in ['records','database','pool']}))
    if summary['state']!='COMPLETE':raise RuntimeError('Benchmark incomplete; inspect original IDs, do not replay uncertain requests')


if __name__=='__main__':main()
