"""Controlled native-runtime capacity matrix; MaaS stays paused until original config restored.
Only selected model replicas/worker_selector change. No VM, worker or Docker daemon operation.
Any exception stops the matrix and leaves admission paused for operator inspection.
"""
import argparse,re,uuid,base64,copy,fcntl,json,os,sys,threading,time,urllib.request
from pathlib import Path
sys.path[:0]=[str(Path(__file__).resolve().parents[3]),'/root/wtaobj']
from maas_platform.cluster_maintenance import model_hash
from benchmark_executors import atomic,command,percentile,bounded_map,worker_metrics
from maas_bench.contracts import integer,parallel_arg
LIVE=Path('/root/wtaobj/maas-live');ROOT=Path('/root/wtaobj/maas-framework');FIXTURES=Path('/root/wtaobj/maas-endurance-fixtures')
COMPOSE=['docker','compose','--env-file',str(LIVE/'app.env'),'-f',str(ROOT/'deploy/maas/compose.yaml')]


def change_model(api,expected,replicas,selector):
    integer(replicas,'replicas',minimum=0)
    path='/v2/models/'+str(expected['id']);current=api(path)
    if current['replicas']!=expected['replicas'] or model_hash(current)!=model_hash(expected):
        raise RuntimeError('Deployment changed externally; refuse overwrite')
    desired=copy.deepcopy(current);desired.update(replicas=replicas,worker_selector=selector)
    result=api(path,desired,'PUT')
    if result['replicas']!=replicas or model_hash(result)!=model_hash(desired):raise RuntimeError('Unconfirmed deployment change; inspect without replay')
    return result


def planned_points(profile,worker_ids,counts,clients,repeats):
    if not worker_ids or len(worker_ids)!=len(set(worker_ids)):raise ValueError('Unique worker IDs required')
    for count in counts:integer(count,'replicas')
    for client in clients:integer(client,'clients')
    if not counts or not clients or not 1<=repeats<=3:raise ValueError('Bounded matrix required')
    if profile=='tuning':base=[('dual-'+str(n)+'-c'+str(c),n,worker_ids,c) for n in counts for c in clients]
    elif profile=='remaining':base=[('dual-'+str(n),n,worker_ids,clients[0]) for n in [4,8]]
    else:base=[('single'+str(i)+'-'+str(n),n,[i],clients[0]) for i in worker_ids for n in [1,2,4]]+[('dual-'+str(n),n,worker_ids,clients[0]) for n in [2,4,8]]
    return [(name+'-r'+str(r+1),n,ids,c) for r in range(repeats) for name,n,ids,c in base]


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run-id',default='native-matrix')
    parser.add_argument('--profile',choices=['all','remaining','tuning'],default='all')
    parser.add_argument('--model-id',type=int,default=18)
    parser.add_argument('--worker-ids',type=int,nargs='+')
    parser.add_argument('--replica-counts',type=parallel_arg,nargs='+',default=[4,6,8])
    parser.add_argument('--clients',type=parallel_arg,nargs='+',default=[4])
    parser.add_argument('--repeats',type=int,choices=range(1,4),default=1)
    args=parser.parse_args()
    if not re.fullmatch(r'[a-z0-9-]{1,60}',args.run_id):parser.error('Bounded run ID required')
    if args.model_id<=0 or (args.worker_ids and any(i<=0 for i in args.worker_ids)):parser.error('Positive model/worker IDs required')
    if os.getuid()!=0:raise RuntimeError('Controller root only')
    from gpustack_admin import api
    folder=LIVE/'e3'/args.run_id;folder.mkdir(mode=0o700,exist_ok=False)
    def cli(*args):return json.loads(command(COMPOSE+['exec','-T','api','python','-m','maas_platform.cli',*args]))
    model_path='/v2/models/'+str(args.model_id);model=api(model_path);integer(model['replicas'],'runtime replicas')
    assert model['env']['GPUSTACK_MODEL_VRAM_CLAIM']=='2147483648'
    original=copy.deepcopy(model);atomic(folder/'original-private.json',original)
    worker_ids=args.worker_ids or sorted({r['worker_id'] for r in api(model_path+'/instances')['items']})
    points=planned_points(args.profile,worker_ids,args.replica_counts,args.clients,args.repeats)
    workers={i:api('/v2/workers/'+str(i)) for i in worker_ids};assert all(w['state']=='ready' for w in workers.values())
    manifest=json.loads((FIXTURES/'manifest.json').read_text());inputs=[]
    for item in manifest:
        raw=(FIXTURES/item['file']).read_bytes();assert hashlib_sha(raw)==item['sha256']
        inputs.append({**item,'url':'data:'+item['media_type']+';base64,'+base64.b64encode(raw).decode()})
    stress_root=LIVE/'e3/stress-fixtures'
    stress_manifest=json.loads((stress_root/'manifest.json').read_text())
    for item in stress_manifest:
        raw=(stress_root/item['file']).read_bytes();assert hashlib_sha(raw)==item['sha256']
        inputs.append({**item,'url':'data:'+item['media_type']+';base64,'+base64.b64encode(raw).decode()})
    key=(LIVE/'secrets/pilot-key').read_text().strip()
    opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
    summary={'run_id':args.run_id,'profile':args.profile,'repeats':args.repeats,'phase':'PREPARED','started_at':time.time(),'original_replicas':original['replicas'],'original_model_sha256':model_hash(original),
        'fixture_manifest_sha256':hashlib_sha((FIXTURES/'manifest.json').read_bytes()),'stress_manifest_sha256':hashlib_sha((stress_root/'manifest.json').read_bytes()),'points':[],
        'model_id':args.model_id,'worker_ids':worker_ids,'scope':'native GPUStack; MaaS paused; cached image/process cold start; no custom collector; fixed-count legacy tool is not a sustained-supply proof'}
    checkpoint_lock=threading.RLock()
    def checkpoint():
        with checkpoint_lock:atomic(folder/'status.json',summary)
    def wait(test,seconds=180):
        end=time.monotonic()+seconds
        while time.monotonic()<end:
            if test():return
            time.sleep(.5)
        raise RuntimeError('Convergence deadline; stop matrix, keep paused')
    def instances():return api(model_path+'/instances')['items']
    stop=threading.Event();abort=threading.Event();sample_errors=[];native_target=[];sample_context={'point':None,'phase':'PREPARED'}
    def sample():
        while not stop.is_set():
            row={'at':time.time(),**sample_context,'workers':{}}
            for ident,w in workers.items():
                try:
                    with opener.open('http://'+w['ip']+':10151/metrics',timeout=2) as r:lines=r.read().decode().splitlines()
                    names=['worker_node_gpu_utilization_rate','worker_node_gram_used_bytes','worker_node_gram_allocated_bytes','worker_node_cpu_utilization_rate','worker_node_memory_used_bytes']
                    row['workers'][str(ident)]=worker_metrics(lines,names)
                    row['workers'][str(ident)]['missing_optional_metrics']=[n for n in names if row['workers'][str(ident)][n] is None]
                except Exception as e:
                    row['workers'][str(ident)]={'error_class':type(e).__name__};sample_errors.append({'worker':ident,'error_class':type(e).__name__});abort.set()
            with (folder/'samples.jsonl').open('a') as f:f.write(json.dumps(row)+'\n')
            stop.wait(.5)
    def native(item):
        if abort.is_set():raise RuntimeError('Matrix halted; do not send another inference')
        record={'image':item['id'],'source':item['source'],'request_id':str(uuid.uuid4()),'state':'SENDING','intent_at':time.time()}
        with checkpoint_lock:native_target.append(record);checkpoint()
        body={'model':original['name'],'stream':False,'messages':[{'role':'user','content':[{'type':'image_url','image_url':{'url':item['url']}}]}]}
        request=urllib.request.Request('http://127.0.0.1:18080/v1/chat/completions',data=json.dumps(body).encode(),headers={'Authorization':'Bearer '+key,'Content-Type':'application/json','X-Request-ID':record['request_id']})
        t=time.monotonic()
        try:
            with opener.open(request,timeout=35) as response:value=json.load(response);assert response.status==200
            lines=json.loads(value['choices'][0]['message']['content'])['lines'];assert isinstance(lines,list)
            with checkpoint_lock:
                record.update(state='SUCCEEDED',http_status=200,seconds=time.monotonic()-t,lines=len(lines),finished_at=time.time());checkpoint()
            return record
        except Exception as exc:
            abort.set()
            with checkpoint_lock:
                record.update(state='FAILED_STOP',error_class=type(exc).__name__,http_status=getattr(exc,'code',None),failed_at=time.time());checkpoint()
            raise
    def guard():
        if abort.is_set():raise RuntimeError('Sampling or inference failed; keep paused')
        status=cli('drain-status')
        if status['generation']!=summary['generation'] or not status['managed_requests_drained']:raise RuntimeError('Pause ownership/UNKNOWN changed; stop experiment')
    checkpoint()
    summary['preflight_records']=native_target
    summary['native_preflight']=native(inputs[0]);checkpoint()
    sampling=None
    with open('/run/maas-global-maintenance.lock','a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        active=json.loads((LIVE/'maintenance/active.json').read_text());cfg=json.loads(Path(active['config']).read_text())
        assert json.loads(Path(cfg['journal']).read_text())['phase']=='RESUMED'
        pool=LIVE/'executors/pending.json'
        if pool.exists():assert json.loads(pool.read_text())['phase']=='COMPLETE'
        assert not cli('drain-status')['paused']
        summary['generation']=cli('pause','--reason','E3 native replica capacity matrix')['generation'];summary['phase']='PAUSED';checkpoint()
        wait(lambda:cli('drain-status')['managed_requests_drained'],90);guard()
        sampling=threading.Thread(target=sample);sampling.start()
        try:
            # First single-GPU points, then dual-GPU points; keep conservative 2GiB claim.
            for name,count,ids,clients in points:
                guard();point={'name':name,'replicas':count,'eligible_workers':ids,'phase':'SCALING_ZERO'};summary['points'].append(point);summary['phase']='EXPERIMENT';checkpoint();sample_context.update(point=name,phase='SCALING_ZERO')
                model=change_model(api,model,0,model['worker_selector']);wait(lambda:not instances())
                point['phase']='PROCESS_COLD_START';sample_context['phase']=point['phase'];checkpoint()
                selector=copy.deepcopy(original['worker_selector'])
                if len(ids)==1:selector['worker-name']=workers[ids[0]]['name']
                guard();start=time.monotonic();model=change_model(api,model,count,selector)
                def ready():
                    rows=instances()
                    if any(r['state']=='error' for r in rows):raise RuntimeError('Model instance error; stop experiment')
                    return len(rows)==count and all(r['state']=='running' and r['worker_id'] in ids for r in rows)
                wait(ready);point['process_start_seconds']=time.monotonic()-start;point['placement']={str(i):sum(r['worker_id']==i for r in instances()) for i in ids}
                point['phase']='WARMUP';sample_context['phase']='WARMUP';checkpoint()
                point['warmup_records']=[];native_target=point['warmup_records']
                for item in inputs[:2]:native(item)
                point['phase']='MEASURE';sample_context['phase']='MEASURE';checkpoint();start=time.monotonic()
                # Independent native client count, separate from model replicas and executor count.
                point['records']=[];native_target=point['records']
                rows=bounded_map(native,inputs,clients,abort)
                point.update(phase='COMPLETE',clients=clients,requests=len(rows),successes=len(rows),elapsed_seconds=time.monotonic()-start,records=rows)
                point.update(throughput_images_per_second=len(rows)/point['elapsed_seconds'],p50_seconds=percentile([r['seconds'] for r in rows],.5),p95_seconds=percentile([r['seconds'] for r in rows],.95),p99_seconds=percentile([r['seconds'] for r in rows],.99));checkpoint()
                print(json.dumps({'point':name,'state':'COMPLETE','placement':point['placement'],'throughput':point['throughput_images_per_second']}),flush=True)
            guard();stop.set();sampling.join(timeout=8);guard();summary['phase']='RESTORING';sample_context.update(point='restore',phase='RESTORING');checkpoint()
            model=change_model(api,model,0,model['worker_selector']);wait(lambda:not instances());guard()
            model=change_model(api,model,original['replicas'],original['worker_selector'])
            wait(lambda:len(instances())==original['replicas'] and all(r['state']=='running' for r in instances()))
            assert model_hash(api(model_path))==model_hash(original)
            summary['restored_instances']=[{'id':r['id'],'worker_id':r['worker_id']} for r in instances()]
            summary['probe']=cli('resume','--credential-id','pilot-ocr','--probe-image','/run/maas-probe/upright.png')
            summary['phase']='COMPLETE';summary['finished_at']=time.time();checkpoint()
        except Exception as exc:
            summary.update(phase='INTERRUPTED_REQUIRES_INSPECTION',error_class=type(exc).__name__,sample_errors=sample_errors,finished_at=time.time());checkpoint()
            raise
        finally:
            stop.set()
            if sampling:sampling.join(timeout=8)
    print(json.dumps({'phase':summary['phase'],'points':len(summary['points']),'restored_replicas':original['replicas']}))


def hashlib_sha(raw):
    import hashlib
    return hashlib.sha256(raw).hexdigest()


if __name__=='__main__':main()
