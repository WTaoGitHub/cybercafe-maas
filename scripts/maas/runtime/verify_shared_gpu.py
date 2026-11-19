"""Finite PP-OCRv4 and OvisOCR3 same-GPU check against an existing test system.

Use the read-only GPUStack URL and key already configured in the environment.
The two inference targets come from the native inventory. The script checks the
exact same-card placement before sending a bounded workload.
"""
import argparse
from base64 import b64encode
from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import re
import threading
import time

import httpx
from PIL import Image


def run(sample, prompt, rounds):

    import httpx

    key = Path(os.environ['E17_GPUSTACK_KEY_FILE']).read_text().strip()
    management = os.environ['MAAS_GPUSTACK_URL'].rstrip('/')+'/v2/'
    def native_client():
        return httpx.Client(base_url=management, headers={'Authorization':'Bearer '+key}, timeout=5,
                            trust_env=False, follow_redirects=False)

    def snapshot(c):
        value={}
        for resource in ('workers','models','model-instances'):
            response=c.get(resource,params={'page':1,'perPage':100})
            response.raise_for_status()
            data=response.json()
            assert len(data['items'])==data['pagination']['total']
            value[resource]={row['id']:row for row in data['items']}
        return value

    def layout(s):
        by_device={}
        for i in s['model-instances'].values():
            name=s['models'][i['model_id']]['name']
            if i['state']!='running' or name not in ('maas-ocr','ovisocr3'):
                continue
            w=s['workers'][i['worker_id']]
            if w['state']!='ready' or w.get('unreachable') or (w.get('maintenance') or {}).get('enabled'):
                continue
            index,=i['gpu_indexes']
            dev,=(g for g in w['status']['gpu_devices'] if g['index']==index)
            base='http://'+(i.get('worker_ip') or w['ip'])+':'+str(i['port'])
            endpoints=by_device.setdefault((w['id'],dev['uuid']),{})
            assert name not in endpoints
            endpoints[name]={'instance':i['id'],'base':base,'runtime':i.get('backend_version'),
                         'restart_count':i.get('restart_count'),'last_restart_time':i.get('last_restart_time')}
        pair,= [(loc,hit) for loc,hit in by_device.items() if hit.keys()=={'maas-ocr','ovisocr3'}]
        return pair

    with native_client() as native:
        before=snapshot(native)
    where, endpoints=layout(before)
    worker_id, gpu_uuid=where
    source_gpu,=(g for g in before['workers'][worker_id]['status']['gpu_devices'] if g['uuid']==gpu_uuid)
    def normalize(count):
        return round(count / 1048576)
    total=normalize(source_gpu['memory']['total'])
    worker=before['workers'][worker_id]
    metrics_base=endpoints['ovisocr3']['base'].rsplit(':',1)[0]+':'+str(worker['metrics_port'])+'/metrics'
    memory=[]
    stopped=threading.Event()
    observer_errors=[]

    def observe_memory():
        with httpx.Client(timeout=3,trust_env=False,follow_redirects=False) as client:
            while not stopped.is_set():
                try:
                    begin=time.monotonic()
                    response=client.get(metrics_base)
                    response.raise_for_status()
                    end=time.monotonic()
                    fields=(('gpustack:worker_node_gram_used_bytes','used'),
                            ('gpustack:worker_node_gram_total_bytes','total'),
                            ('gpustack:worker_node_gram_allocated_bytes','allocated'))
                    values={}
                    for metric,name in fields:
                        readings=[]
                        for line in response.text.splitlines():
                            if not line.startswith(metric+'{'):
                                continue
                            labels, numeric=line.split('}',1)
                            if f'worker_id="{worker_id}"' in labels and f'gpu_index="{source_gpu["index"]}"' in labels:
                                readings.append(float(numeric.strip()))
                        count,=readings
                        values[name]=normalize(count)
                    assert abs(total-values['total']) <= 1
                    memory.append((begin,end,values['used'],values['allocated']))
                except (httpx.HTTPError,ValueError,KeyError,TypeError,AssertionError) as error:
                    observer_errors.append(type(error).__name__)
                    stopped.set()
                stopped.wait(.12)

    def metrics(client):
        start=time.monotonic()
        response=client.get(endpoints['ovisocr3']['base']+'/metrics',timeout=2)
        response.raise_for_status()
        end=time.monotonic()
        def value(metric):
            match=re.findall(r'^vllm:'+metric+r'(?:\{[^\n]*\})?\s+([0-9.eE+-]+)$',response.text,re.M)
            assert match, metric
            return sum(float(val) for val in match)
        return start,end,value('num_requests_running'),value('num_requests_waiting')

    headers={'Accept-Encoding':'identity'}
    image_part={'type':'image_url','image_url':{'url':'data:image/png;base64,'+sample}}
    with httpx.Client(timeout=3,trust_env=False,follow_redirects=False,headers=headers) as http:
        health={}
        for which in endpoints:
            out=http.get(endpoints[which]['base']+'/health')
            out.raise_for_status()
            if which=='maas-ocr':
                health=out.json()
                assert all('CUDAExecutionProvider' in value for value in health['providers'].values())
        assert metrics(http)[2:]==(0,0)

    def infer(name):
        body={'model':name,'stream':False,'messages':[{'role':'user','content':[image_part]}]}
        if name=='ovisocr3':
            body.update(temperature=0,max_tokens=4096,chat_template_kwargs={'enable_thinking':False})
            body['messages'][0]['content'].append({'type':'text','text':prompt})
        with httpx.Client(timeout=14,trust_env=False,follow_redirects=False,headers=headers) as client:
            start=time.monotonic()
            response=client.post(endpoints[name]['base']+'/v1/chat/completions',json=body)
            finished=time.monotonic()
            response.raise_for_status()
            choice,=response.json()['choices']
            assert choice.get('finish_reason')=='stop'
            content=choice['message']['content']
            elapsed=None
            if name=='maas-ocr':
                answer=json.loads(content)
                assert answer['node']==health['node']
                elapsed=answer['elapsed_ms']/1000
                content=answer['text']
            assert '128' in content and '12 minutes' in content
            return {'started':start,'ended':finished,'latency_ms':round((finished-start)*1000),
                    'pp_server_duration':elapsed,'text_chars':len(content)}

    sampler=threading.Thread(target=observe_memory)
    sampler.start()
    def memory_max():
        return max((row[2] for row in memory), default=normalize(source_gpu['memory']['used']))
    samples=[]
    try:
        time.sleep(.65)
        assert memory and not observer_errors
        initial_usage=memory[0][2]
        singles={name:infer(name) for name in endpoints}
        with httpx.Client(timeout=3,trust_env=False,follow_redirects=False,headers=headers) as probe, ThreadPoolExecutor(max_workers=2) as pool:
            for _ in range(rounds):
                assert not observer_errors and memory_max()<total*.9
                assert metrics(probe)[2:]==(0,0)
                ovis=pool.submit(infer,'ovisocr3')
                deadline=time.monotonic()+8
                events=[]
                while not ovis.done() and time.monotonic()<deadline:
                    item=metrics(probe)
                    events.append(item)
                    if item[2]==1 and item[3]==0:
                        break
                    time.sleep(.022)
                else:
                    raise RuntimeError('Ovis request active window could not be observed')
                pp=pool.submit(infer,'maas-ocr')
                while not pp.done():
                    events.append(metrics(probe))
                    time.sleep(.022)
                ovis_res,pp_res=ovis.result(),pp.result()
                dur=pp_res['pp_server_duration']
                left,right=pp_res['ended']-dur,pp_res['started']+dur
                strict=sum(left<=a<=b<=right and running==1 and queued==0 for a,b,running,queued in events)
                concurrent=sum(pp_res['started']<=a<=b<=pp_res['ended'] and running==1 and queued==0
                               for a,b,running,queued in events)
                assert metrics(probe)[2:]==(0,0)
                samples.append({'pp_ms':pp_res['latency_ms'],'ovis_ms':ovis_res['latency_ms'],
                    'pp_server_ms':round(dur*1000),'pp_window_observations':strict,'inflight_observations':concurrent,
                    'vram_reads_during_pp':sum(pp_res['started']<=a<=b<=pp_res['ended'] for a,b,*_ in memory),
                    'ovis_running_during_vram_read':sum(ovis_res['started']<=a<=b<=ovis_res['ended'] for a,b,*_ in memory)})
                time.sleep(.5)
    finally:
        stopped.set()
        sampler.join(timeout=6)
    assert not sampler.is_alive() and not observer_errors and memory
    assert all(sample['pp_window_observations'] > 0 for sample in samples)
    assert all(sample['ovis_running_during_vram_read'] > 0 for sample in samples)
    assert memory_max() < total*.9
    with native_client() as native:
        assert layout(snapshot(native))==(where,endpoints)
    print(json.dumps({'worker':worker_id,'gpu':source_gpu['name'],'total_mib':total,'baseline_mib':initial_usage,
                      'max_observed_mib':memory_max(),'memory_sample_count':len(memory),
                      'allocated_mib':max(row[3] for row in memory),
                      'fastest_scrape_ms':round(min(end-begin for begin,end,*_ in memory)*1000),
                      'slowest_scrape_ms':round(max(end-begin for begin,end,*_ in memory)*1000),
                      'baseline_ms':{name:row['latency_ms'] for name,row in singles.items()},
                      'pp_health_node_matches_gpu_uuid':health['node']==gpu_uuid,
                      'target_instances':{name:value['instance'] for name,value in endpoints.items()},
                      'rounds':samples, 'max_per_model_inflight':1, 'native_only':True}))

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--image', type=Path, required=True, help='Synthetic PNG containing 128 and 12 minutes')
    parser.add_argument('--prompt-file', type=Path, required=True, help='Plain OCR prompt from the pinned OvisOCR3 package')
    parser.add_argument('--rounds', type=int, choices=range(1, 7), default=3)
    parser.add_argument('--execute', action='store_true')
    args = parser.parse_args()
    if not args.execute:
        print(json.dumps({'state': 'DRY_RUN', 'execution': False}))
        return
    if not __debug__:
        parser.error('Run with assertions enabled')
    with Image.open(args.image) as image:
        if image.format != 'PNG' or max(image.size) > 1440:
            parser.error('Use a PNG within the tested 1440 × 1440 profile')
    sample = b64encode(args.image.read_bytes()).decode()
    run(sample, args.prompt_file.read_text().strip(), args.rounds)


if __name__ == '__main__':
    main()
