"""Run on 001. Synthetic fixture only; not a production accuracy benchmark."""
import base64
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import statistics
import time
import urllib.request
from gpustack_admin import api, opener, base

encoded = base64.b64encode(Path('/root/wtaobj/ocr-fixture.png').read_bytes()).decode()

def request(url, body, client=None):
    req = urllib.request.Request(url, data=json.dumps(body).encode(), headers={'Content-Type':'application/json'})
    response = client.open(req, timeout=120) if client else urllib.request.urlopen(req, timeout=120)
    with response as resp:
        return json.load(resp)

def run(url, body, count, concurrency, client=None):
    def one(_):
        start = time.perf_counter()
        result = request(url, body, client)
        if 'choices' in result:
            result = json.loads(result['choices'][0]['message']['content'])
        assert 'GPU OCR TEST' in result['text'], result
        return {'seconds':time.perf_counter()-start,'node':result.get('node'),'lines':len(result.get('lines',[]))}
    one(0)
    start = time.perf_counter()
    with ThreadPoolExecutor(max_workers=concurrency) as pool:
        results = list(pool.map(one, range(count)))
    seconds = time.perf_counter()-start
    return {'requests':count,'concurrency':concurrency,'seconds':seconds,'images_per_second':count/seconds,
            'median_latency_seconds':statistics.median(x['seconds'] for x in results),
            'nodes':{str(n):sum(x['node']==n for x in results) for n in set(x['node'] for x in results)},
            'line_counts':sorted(set(x['lines'] for x in results))}

if __name__ == '__main__':
    body = {'model':'rapidocr-gpu','messages':[{'role':'user','content':[
        {'type':'image_url','image_url':{'url':'data:image/png;base64,'+encoded}}]}]}
    reports = {}
    reports['cpu_existing'] = run('http://127.0.0.1:8820/ocr', {'image_base64':encoded}, 8, 1)
    reports['gpu_via_gpustack'] = run(base+'/v1/chat/completions', body, 16, 4, opener)
    dest = Path('/root/wtaobj/ocr-verification.json')
    dest.write_text(json.dumps(reports,indent=2))
    print(dest.read_text())
