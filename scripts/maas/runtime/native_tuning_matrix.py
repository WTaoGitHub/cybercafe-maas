"""T1 timed native requests under an owned MaaS pause; no VM/worker restarts."""
import argparse
import base64
import copy
import fcntl
import hashlib
import json
import math
import os
from pathlib import Path
import sys
import threading
import time
import uuid

sys.path.insert(0,str(Path(__file__).resolve().parents[3]))
from maas_bench.prepared import timed_prepared
from maas_bench.snapshot import read_private,read_secret,save_private,digest,RuntimeFence
from maas_platform.cluster_maintenance import model_hash
from replica_capacity_matrix import change_model
from executor_pool import LIVE,LOCK,cli,inventory
from benchmark_executors import atomic,percentile
from maas_bench.experiment import stamp,check_proof,check_runtime,fresh_namespace



def text_fingerprint(lines):
    if not isinstance(lines,list):raise ValueError('OCR lines missing')
    for line in lines:
        if not isinstance(line,dict) or not isinstance(line.get('text'),str):raise ValueError('OCR text schema mismatch')
        box=line.get('box');score=line.get('score')
        if (not isinstance(box,list) or len(box)!=4 or any(not isinstance(p,list) or len(p)!=2 for p in box)
                or any(type(x) not in (int,float) or not math.isfinite(x) for p in box for x in p)
                or type(score) not in (int,float) or not math.isfinite(score) or not 0<=score<=1):
            raise ValueError('OCR geometry/score schema mismatch')
    return hashlib.sha256('\n'.join(r['text'] for r in lines).encode()).hexdigest()


def verify_prerequisite(config):
    path=Path(config['baseline_proof_file']);raw=path.read_bytes()
    if hashlib.sha256(raw).hexdigest()!=config['baseline_proof_sha256']:raise ValueError('Baseline proof hash changed')
    proof=json.loads(raw)
    if proof.get('case_id')!='T0-BASELINE' or proof.get('state')!='COMPLETE' or proof.get('profile_id')!=config['profile_id']:
        raise ValueError('Completed matching T0 baseline required')
    check_proof(proof,config)
    quality=read_private(config['expected_text_file'])
    if quality['input_sha256']!=proof['input_sha256'] or quality['profile_id']!=proof['profile_id']:
        raise ValueError('Different input/quality profile')
    if config.get('replica_counts')!=[4,6,8] or config.get('clients')!=[4,6,8,12]:raise ValueError('Unapproved T1 point scope')
    return proof,quality


def execute(config):
    import httpx
    sys.path.insert(0,'/root/wtaobj')
    from gpustack_admin import api
    from maas_bench.runtime import ControllerProbe
    proof,quality=verify_prerequisite(config)
    if os.getuid()!=0:raise RuntimeError('Controller root required')
    root=Path(config['output_directory']);root.mkdir(mode=0o700,exist_ok=False)
    state={'case_id':'T1-NATIVE','profile_id':proof['profile_id'],'input_sha256':proof['input_sha256'],
        'state':'PREPARED',**stamp(config),'points':[],'events':[],'source_baseline_sha256':config['baseline_proof_sha256']}
    journal_lock=threading.RLock();abort=threading.Event();sampler_stop=threading.Event();sample_context={'phase':'PREPARED'}
    sample_errors=[]
    def checkpoint():
        with journal_lock:atomic(root/'status.json',state)
    def event(phase,**values):
        with journal_lock:state['events'].append({'phase':phase,'at':time.time(),**values});state['state']=phase;checkpoint()
    model_path='/v2/models/'+str(config['model_id']);original=api(model_path);model=copy.deepcopy(original)
    if model['replicas']!=proof['parameters']['replicas'] or model['env']['GPUSTACK_MODEL_VRAM_CLAIM']!=str(proof['parameters']['vram_claim_bytes']):
        raise RuntimeError('Runtime baseline differs from proof')
    probe=ControllerProbe(Path(__file__).resolve().parents[3],config['model_id'],config['profile_id'],proof['input_sha256'])
    baseline=probe.capture();check_runtime(config,baseline);save_private(root/'original-model-private.json',original);save_private(root/'runtime-private.json',baseline)
    RuntimeFence(read_private(config['baseline_runtime_snapshot_file'])).check(baseline)
    if not read_private(config['mac_backup_receipt_file']).get('mac_private_backup_complete'):raise RuntimeError('Verified Mac backup required')
    workers={w['id']:w for w in baseline['workers']};assert len(workers)==2
    manifest_file=Path(config['raw_manifest_file']);manifest=json.loads(manifest_file.read_text());inputs=[]
    if hashlib.sha256(manifest_file.read_bytes()).hexdigest()!=read_private(config['prepared_fixture_file'])['manifest_sha256']:
        raise RuntimeError('Raw/preuploaded input manifest differs')
    for item in manifest:
        raw=(manifest_file.parent/item['file']).read_bytes()
        if hashlib.sha256(raw).hexdigest()!=item['sha256'] or item['id'] not in quality['items']:raise RuntimeError('Input manifest changed')
        url='data:'+item['media_type']+';base64,'+base64.b64encode(raw).decode()
        body={'model':original['name'],'stream':False,'messages':[{'role':'user','content':[{'type':'image_url','image_url':{'url':url}}]}]}
        inputs.append({'id':item['id'],'body':json.dumps(body).encode()})
    if len(inputs)!=40:raise RuntimeError('Fixed 40-image profile required')
    checkpoint();http=httpx.Client(timeout=35,trust_env=False,follow_redirects=False,
        transport=httpx.HTTPTransport(retries=0),limits=httpx.Limits(max_connections=16,max_keepalive_connections=16),
        headers={'Authorization':'Bearer '+read_secret(config['customer_key_file']),'Content-Type':'application/json'})
    intent_log=os.fdopen(os.open(root/'requests.jsonl',os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600),'w')
    def log(row):
        with journal_lock:intent_log.write(json.dumps(row)+'\n');intent_log.flush();os.fsync(intent_log.fileno())
    def request(item,sequence):
        if abort.is_set():raise RuntimeError('Matrix stopped; no request')
        rid=uuid.uuid4().hex;log({'kind':'intent','request_id':rid,'sequence':sequence,'image':item['id'],'point':sample_context.get('point'),'phase':sample_context['phase'],'at':time.time()})
        try:
            r=http.post('http://127.0.0.1:18080/v1/chat/completions',content=item['body'],headers={'X-Request-ID':rid})
            if r.status_code!=200:raise RuntimeError('Native status '+str(r.status_code))
            lines=json.loads(r.json()['choices'][0]['message']['content'])['lines'];sha=text_fingerprint(lines)
            if sha!=quality['items'][item['id']]['text_sha256']:raise RuntimeError('Text differs from fixed baseline')
            result={'request_id':rid,'image':item['id'],'http_status':200,'lines':len(lines),'text_sha256':sha}
            log({'kind':'response','at':time.time(),**result});return result
        except Exception as exc:
            abort.set();log({'kind':'failed','request_id':rid,'error_class':type(exc).__name__,'at':time.time()});raise
    def guard():
        if abort.is_set():raise RuntimeError('Native/sampling error; keep paused')
        s=cli('drain-status')
        if s['generation']!=state['generation'] or not s['managed_requests_drained']:
            raise RuntimeError('Pause ownership/activity changed')
    def wait_ready(count,deadline_seconds=180):
        until=time.monotonic()+deadline_seconds
        while time.monotonic()<until:
            guard();rows=api(model_path+'/instances')['items']
            if any(r['state']=='error' for r in rows):raise RuntimeError('Native instance error')
            if len(rows)==count and all(r['state']=='running' and r['worker_id'] in workers for r in rows):
                placement={str(i):sum(r['worker_id']==i for r in rows) for i in workers}
                if len(set(placement.values()))!=1:raise RuntimeError('Replica placement is not balanced')
                return placement
            time.sleep(.5)
        raise RuntimeError('Model convergence deadline')
    def sample():
        high_cpu_since=None
        while not sampler_stop.is_set():
            try:
                guard();phase=sample_context['phase'];metrics=probe.resources(baseline)
                if phase in ('MEASURE','WARMUP'):
                    expected=copy.deepcopy(model);current=api(model_path)
                    if sample_context['phase']==phase and (current['replicas']!=expected['replicas'] or model_hash(current)!=model_hash(expected)):
                        raise RuntimeError('External native model drift')
                for row in metrics:
                    w=workers[row['worker_id']];ram=w['ram_total']-row['worker_node_memory_used_bytes']
                    gpu=w['gpus'][0]['memory_total']-row['worker_node_gram_used_bytes']
                    if ram<max(2*1024**3,.1*w['ram_total']) or gpu<max(1024**3,.1*w['gpus'][0]['memory_total']):
                        raise RuntimeError('Native resource headroom below approved floor')
                cpu=max(r['worker_node_cpu_utilization_rate'] for r in metrics)
                high_cpu_since=(high_cpu_since or time.monotonic()) if cpu>=80 else None
                if high_cpu_since and time.monotonic()-high_cpu_since>=30:raise RuntimeError('Sustained native CPU pressure')
                with (root/'resources.jsonl').open('a') as f:f.write(json.dumps({'at':time.time(),'phase':phase,'point':sample_context.get('point'),'workers':metrics})+'\n')
            except Exception as exc:
                with journal_lock:sample_errors.append({'error_class':type(exc).__name__})
                abort.set();return
            sampler_stop.wait(.5)
    sampling=None
    try:
        with open(LOCK,'a') as lock:
            fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
            active=json.loads((LIVE/'maintenance/active.json').read_text());cfg=json.loads(Path(active['config']).read_text())
            if json.loads(Path(cfg['journal']).read_text())['phase']!='RESUMED':raise RuntimeError('Unfinished maintenance')
            pending=LIVE/'executors/pending.json'
            if pending.exists() and json.loads(pending.read_text()).get('phase')!='COMPLETE':raise RuntimeError('Unfinished executor change')
            initial=cli('drain-status')
            if initial['paused'] or initial['active_tasks'] or initial['backend_activity'] or initial['sync_activity']:raise RuntimeError('Exclusive idle window required')
            paused=cli('pause','--reason','E3-TUNE T1 native matrix');state['generation']=paused['generation'];event('PAUSED')
            until=time.monotonic()+90
            while not cli('drain-status')['managed_requests_drained']:
                if time.monotonic()>until:raise RuntimeError('Drain deadline')
                time.sleep(.5)
            sampling=threading.Thread(target=sample,name='native-guard');sampling.start()
            for count in config['replica_counts']:
                guard();sample_context['phase']='SCALING'
                if count!=model['replicas']:
                    model=change_model(api,model,0,original['worker_selector'])
                    wait_ready(0)
                    model=change_model(api,model,count,original['worker_selector'])
                placement=wait_ready(count)
                sample_context['phase']='WARMUP'
                for i in range(count):request(inputs[i%40],-i-1)
                for clients in config['clients']:
                    guard();point_id='T1-NATIVE-'+str(count)+'-'+str(clients)
                    point={'point_id':point_id,'replicas':count,'clients':clients,'placement':placement,'state':'RUNNING'}
                    with journal_lock:state['points'].append(point);checkpoint()
                    sample_context.update(phase='MEASURE',point=point_id)
                    result=timed_prepared(request,inputs,clients,seconds=60,min_successes=200,max_seconds=300,stop=abort)
                    if result['state']!='COMPLETE' or sample_errors:raise RuntimeError('Native point invalid: '+result['state'])
                    sample_context['phase']='BETWEEN_POINTS';guard()
                    values=[r['seconds'] for r in result['records'] if r['completed_offset']<=result['measurement_seconds']]
                    point.update({k:v for k,v in result.items() if k!='records'},p50_seconds=percentile(values,.5),p95_seconds=percentile(values,.95),p99_seconds=percentile(values,.99))
                    save_private(root/(point_id+'.json'),result);checkpoint()
                    print(json.dumps({k:point[k] for k in ('point_id','state','measurement_seconds','measured_successes','throughput_per_second')}),flush=True)
            sample_context['phase']='RESTORING';guard()
            model=change_model(api,model,0,original['worker_selector']);wait_ready(0)
            model=change_model(api,model,original['replicas'],original['worker_selector']);wait_ready(original['replicas'])
            if model_hash(api(model_path))!=model_hash(original):raise RuntimeError('Restored model differs')
            guard();sampler_stop.set();sampling.join(timeout=10)
            state['probe']=cli('resume','--credential-id','pilot-ocr','--probe-image','/run/maas-probe/upright.png')
            event('COMPLETE');print(json.dumps({'case_id':'T1-NATIVE','state':'COMPLETE','points':len(state['points'])}),flush=True)
    except BaseException as exc:
        abort.set();event('INTERRUPTED_REQUIRES_INSPECTION',error_class=type(exc).__name__)
        state['sample_errors']=sample_errors;checkpoint();raise
    finally:
        sampler_stop.set()
        if sampling:sampling.join(timeout=10)
        http.close();intent_log.close()
    return state


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--config',required=True);p.add_argument('--execute',action='store_true');a=p.parse_args()
    config=read_private(a.config);verify_prerequisite(config)
    if not a.execute:print(json.dumps({'state':'DRY_RUN','point_count':12}));return
    execute(config)


if __name__=='__main__':main()
