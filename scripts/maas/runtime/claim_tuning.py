"""T5 container-cold startup and boundary inference; retain the 2GiB claim."""
import argparse
import base64
import copy
import fcntl
import hashlib
import json
import os
from pathlib import Path
import sys
import threading
import time
sys.path.insert(0,str(Path(__file__).resolve().parents[3]))
import httpx
from maas_bench.phase import Phase,proof_entry
from maas_bench.snapshot import read_private,read_secret,save_private,RuntimeFence,digest
from maas_bench.resources import ResourceGuard
from maas_bench.contracts import Parameters
from maas_platform.cluster_maintenance import model_hash
from replica_capacity_matrix import change_model
from native_tuning_matrix import text_fingerprint


def run(config):
    template=read_private(config['template_file']);queue,_=proof_entry(config['queue_proof_file'],'T4-QUEUE',template)
    extend,_=proof_entry(config['extend_proof_file'],'T3-EXTEND',template,('COMPLETE','SKIPPED_CONDITION_NOT_MET'))
    candidates=[]
    for c in extend['screened_candidates']:
        params={**c['parameters'],**{k:queue['selected_candidate']['parameters'][k] for k in ('global_queue','project_queue')}}
        if params['vram_claim_bytes']!=2147483648:raise ValueError('This experiment only retains 2GiB')
        candidates.append({**c,'parameters':params})
    points=[{'point_id':'T5-retain-'+str(n),'parameters':c['parameters']} for n,c in enumerate(candidates)]
    phase=Phase(config,'T5-CLAIM',points,[('T4-QUEUE',config['queue_proof_file'],('COMPLETE',)),('T4-HTTP-RECOVER',config['http_recovery_proof_file'],('COMPLETE',))])
    manifest_file=Path(config['boundary_manifest_file']);manifest=json.loads(manifest_file.read_text());inputs=[]
    if len(manifest)!=10 or len({r['id'] for r in manifest})!=10 or hashlib.sha256(manifest_file.read_bytes()).hexdigest()!=config['boundary_manifest_sha256']:
        raise ValueError('Fixed ten hashed boundary fixtures required')
    for item in manifest:
        raw=(manifest_file.parent/item['file']).read_bytes()
        if len(raw)>10485760 or item['pixels']>20000000 or hashlib.sha256(raw).hexdigest()!=item['sha256']:raise ValueError('Boundary input changed or exceeded envelope')
        inputs.append((item,raw))
    baseline=copy.deepcopy(phase.controller.expected);current=copy.deepcopy(baseline['model']);sampling_halt=threading.Event();abort=threading.Event();errors=[];resources=[]
    stage={'phase':'PREPARED'};sample_file=phase.output/'resources-private.jsonl';sample_file.touch(mode=0o600)
    def guard():
        state=phase.controller.pool.cli('drain-status')
        if state['generation']!=stage['generation'] or not state['managed_requests_drained']:raise RuntimeError('T5 maintenance ownership changed')
        if abort.is_set():raise RuntimeError('T5 resource guard failed')
    def sample():
        resource=ResourceGuard(baseline['workers'])
        try:
            while not sampling_halt.is_set():
                guard();rows=phase.probe.resources(baseline);host=resource.controller();resource.check(rows,host,time.monotonic())
                record={'at_epoch':time.time(),'phase':stage['phase'],'workers':rows,'controller':host};resources.append(record)
                with sample_file.open('a') as f:f.write(json.dumps(record)+'\n')
                sampling_halt.wait(.1)
        except BaseException as exc:errors.append(type(exc).__name__);abort.set()
    def wait_ready(count):
        until=time.monotonic()+180
        while True:
            guard();m=phase.controller.api('/v2/models/'+str(current['id']))
            if model_hash(m)!=model_hash(current) or m['replicas']!=count:raise RuntimeError('T5 model drift')
            rows=phase.controller.api('/v2/models/'+str(current['id'])+'/instances')['items']
            if any(r['state']=='error' for r in rows):raise RuntimeError('T5 instance error')
            if len(rows)==count and all(r['state']=='running' for r in rows):
                counts=[sum(r['worker_id']==w['id'] for r in rows) for w in baseline['workers']]
                if len(set(counts))!=1:raise RuntimeError('Unbalanced T5 placement')
                return
            if time.monotonic()>until:raise RuntimeError('T5 convergence deadline')
            time.sleep(.25)
    quality={};sampler=None
    try:
        pool=phase.controller.pool
        with open(pool.LOCK,'a') as lock:
            fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB);phase.controller.quiet();RuntimeFence(baseline).check(phase.probe.capture())
            active=json.loads((pool.LIVE/'maintenance/active.json').read_text());cfg=json.loads(Path(active['config']).read_text())
            if json.loads(Path(cfg['journal']).read_text())['phase']!='RESUMED':raise RuntimeError('Unfinished maintenance')
            if (pool.LIVE/'executors/pending.json').exists() and json.loads((pool.LIVE/'executors/pending.json').read_text())['phase']!='COMPLETE':raise RuntimeError('Unfinished executor change')
            stage['generation']=pool.cli('pause','--reason','E3 T5 container-cold and boundary measurement')['generation'];guard()
            sampler=threading.Thread(target=sample);sampler.start()
            with httpx.Client(timeout=35,trust_env=False,transport=httpx.HTTPTransport(retries=0),headers={'Authorization':'Bearer '+read_secret(template['customer_key_file'])}) as client:
                requests=phase.output/'native-requests-private.jsonl';requests.touch(mode=0o600)
                for count in (2,max(c['parameters']['replicas'] for c in candidates)):
                    stage['phase']='CONTAINER_COLD_'+str(count)
                    current=change_model(phase.controller.api,current,0,current['worker_selector']);wait_ready(0)
                    current=change_model(phase.controller.api,current,count,current['worker_selector']);wait_ready(count)
                    stage['phase']='BOUNDARY_'+str(count)
                    for item,raw in inputs:
                        guard();intent={'phase':stage['phase'],'image':item['id'],'kind':'intent','at_epoch':time.time()}
                        with requests.open('a') as f:f.write(json.dumps(intent)+'\n');f.flush();os.fsync(f.fileno())
                        response=client.post('http://127.0.0.1:18080/v1/chat/completions',json={'model':current['name'],'stream':False,'messages':[{'role':'user','content':[{'type':'image_url','image_url':{'url':'data:'+item['media_type']+';base64,'+base64.b64encode(raw).decode()}}]}]})
                        if response.status_code!=200:raise RuntimeError('Boundary inference failed, do not replay')
                        value=json.loads(response.json()['choices'][0]['message']['content']);sha=text_fingerprint(value['lines'])
                        if item['id']=='blank-9mp' and value['lines']:raise RuntimeError('Blank boundary returned spurious text')
                        if item['id'] in quality and quality[item['id']]['text_sha256']!=sha:raise RuntimeError('Boundary text changed across instance counts')
                        quality[item['id']]={'text_sha256':sha,'line_count':len(value['lines']),'reference_type':'same-model regression only, not accuracy gold'}
                        with requests.open('a') as f:f.write(json.dumps({'kind':'response','phase':stage['phase'],'image':item['id'],'text_sha256':sha,'request_id':response.headers.get('x-request-id'),'at_epoch':time.time()})+'\n')
            guard();sampling_halt.set();sampler.join(20)
            if errors:raise RuntimeError('T5 sampling failed')
            pool.cli('resume','--credential-id','pilot-ocr','--probe-image','/run/maas-probe/upright.png')
        after=phase.probe.capture();phase.controller.check_fixed(after)
        if after['generation']!=baseline['generation']+2:raise RuntimeError('T5 generation changed externally')
        phase.controller.expected=after
        ordinary=read_private(template['fixture_file']);expected=read_private(template['expected_text_file']);mixed=copy.deepcopy(ordinary)
        mixed['workload']='ordinary80_boundary20';mixed['boundary_manifest_sha256']=hashlib.sha256(manifest_file.read_bytes()).hexdigest()
        raw_ordinary=json.loads(Path(template['raw_manifest_file']).read_text());raw_mixed=[]
        for item in raw_ordinary:raw_mixed.append({**item,'file':str(Path(template['raw_manifest_file']).parent/item['file'])})
        with httpx.Client(base_url=template['base_url'],timeout=15,trust_env=False,transport=httpx.HTTPTransport(retries=0,verify=template['ca_file']),headers={'Authorization':'Bearer '+read_secret(template['customer_key_file'])}) as client:
            upload_log=phase.output/'uploads-private.jsonl';upload_log.touch(mode=0o600)
            for item,raw in inputs:
                with upload_log.open('a') as f:f.write(json.dumps({'kind':'intent','image':item['id'],'sha256':item['sha256'],'at_epoch':time.time()})+'\n');f.flush();os.fsync(f.fileno())
                response=client.post('/maas/v1/artifacts',content=raw,headers={'Content-Type':item['media_type']})
                if response.status_code!=201:raise RuntimeError('Boundary upload failed; preserve response without replay')
                with upload_log.open('a') as f:f.write(json.dumps({'kind':'response','image':item['id'],'artifact_id':response.json()['id'],'at_epoch':time.time()})+'\n')
                mixed['fixtures'].append({'id':item['id'],'source':'boundary','sha256':item['sha256'],'input':{'image':{'artifact_id':response.json()['id']}},'parameters':{}})
                expected['items'][item['id']]=quality[item['id']];raw_mixed.append({**item,'file':str(manifest_file.parent/item['file'])})
        expected['input_sha256']=digest(mixed)
        save_private(phase.output/'mixed-fixtures-private.json',mixed);save_private(phase.output/'mixed-quality-private.json',expected);save_private(phase.output/'mixed-raw-private.json',raw_mixed)
        for point in points:
            phase.controller.change(Parameters(**point['parameters']))
            phase.completed({'point_id':point['point_id'],'state':'COMPLETE','parameters':point['parameters'],'claim_changed':False})
        card_peaks={}
        for sample_row in resources:
            for w in sample_row['workers']:card_peaks[str(w['worker_id'])]=max(card_peaks.get(str(w['worker_id']),0),w['worker_node_gram_used_bytes'])
        phase.finish(final_candidates=candidates,claim_decision='RETAIN_2GiB; no placement need demonstrated; lowering claim at fixed R does not reduce actual use',
            lower_claim='SKIPPED_NO_PLACEMENT_NEED_AND_NO_PER_PROCESS_PEAK_EVIDENCE',
            native_card_max_observed_bytes=card_peaks,per_process_peak_proven=False,
            metric_limit='HTTP polling does not increase freshness of GPUStack native gauges; observed card maxima are not guaranteed startup/process peaks',
            startup_scope='inference container cold, local images and weights warm; no VM/worker/Docker daemon restart',
            boundary_count=10,mixed_workload_sha256=digest(mixed),mixed_workload_fixture_file=str(phase.output/'mixed-fixtures-private.json'),
            mixed_quality_file=str(phase.output/'mixed-quality-private.json'),mixed_raw_file=str(phase.output/'mixed-raw-private.json'))
    except BaseException as exc:phase.interrupted(exc);raise
    finally:
        sampling_halt.set()
        if sampler:sampler.join(20)
        phase.close()


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--config',required=True);p.add_argument('--execute',action='store_true');a=p.parse_args()
    if a.execute:run(read_private(a.config))
    else:print(json.dumps({'state':'DRY_RUN','execution':False}))
