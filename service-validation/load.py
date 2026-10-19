"""Controller HTTP load test against the isolated GPUStack model route."""
import argparse,base64,collections,concurrent.futures,hashlib,json,mimetypes,random,statistics,time,urllib.error,urllib.request
from pathlib import Path
ROOT=Path('/root/wtaobj/service-validation');NAME='wta-ocr-validation'
def pct(xs,q):
 if not xs:return None
 v=sorted(xs);pos=(len(v)-1)*q;i=int(pos);return round(v[i]+(v[min(i+1,len(v)-1)]-v[i])*(pos-i),2)
def main():
 p=argparse.ArgumentParser();p.add_argument('--replicas',type=int,required=True);p.add_argument('--phase',choices=['matrix','sustain'],default='matrix');p.add_argument('--variant');p.add_argument('--concurrency',type=int,default=4);p.add_argument('--seconds',type=float,default=120);a=p.parse_args()
 key=(ROOT/'.benchmark-key').read_text().strip();data=ROOT/'dataset';manifest={r['id']:r for r in map(json.loads,(data/'manifest.jsonl').read_text().splitlines())};split=json.loads((data/'splits.json').read_text())['evaluation']
 pool={}
 for source in ['doclaynet','rctw17']:
  ids=[i for i in split if manifest[i]['source']==source];random.Random(20261019).shuffle(ids);pool[source]=ids[:20]
 chosen=sum(pool.values(),[]);payloads={}
 for ident in chosen:
  m=manifest[ident];raw=(data/m['image']).read_bytes();assert hashlib.sha256(raw).hexdigest()==m['sha256'];payloads[ident]='data:'+mimetypes.guess_type(m['image'])[0]+';base64,'+base64.b64encode(raw).decode()
 output=ROOT/'load-results';output.mkdir(parents=True,exist_ok=True)
 def one(ident,variant):
  body={'model':NAME,'stream':False,'ocr_variant':variant,'messages':[{'role':'user','content':[{'type':'text','text':'ocr_variant='+variant+';rotation_cw=0'},{'type':'image_url','image_url':{'url':payloads[ident]}}]}]}
  request=urllib.request.Request('http://127.0.0.1:18080/v1/chat/completions',data=json.dumps(body).encode(),headers={'Content-Type':'application/json','Authorization':'Bearer '+key})
  start=time.perf_counter();row={'id':ident,'source':manifest[ident]['source'],'variant':variant,'start_unix':time.time()}
  try:
   with urllib.request.urlopen(request,timeout=120) as response:result=json.loads(json.load(response)['choices'][0]['message']['content'])
   assert result['variant']==variant,(result.get('variant'),variant)
   row.update(ok=True,node=result['node'],lines=len(result['lines']),text_sha256=hashlib.sha256(result['text'].encode()).hexdigest(),processing_ms=result['processing_ms'],queue_ms=result['queue_ms'],direction_stats=result.get('direction_stats',{}))
  except Exception as exc:row.update(ok=False,error=type(exc).__name__+': '+str(exc))
  row['end_unix']=time.time();row['client_ms']=round((time.perf_counter()-start)*1000,2);return row
 def run(variant,source,c,count=40,seconds=None):
  ids=pool[source] if source!='mixed' else [x for pair in zip(pool['doclaynet'],pool['rctw17']) for x in pair]
  for i in range(4):
   warm=one(ids[i%len(ids)],variant)
   if not warm['ok']:raise RuntimeError('Warmup failed: '+str(warm))
  start=time.time();counter=iter(range(10**9));rows=[]
  if seconds:
   deadline=time.monotonic()+seconds
   def worker():
    values=[]
    while time.monotonic()<deadline:
     i=next(counter);values.append(one(ids[i%len(ids)],variant))
    return values
   with concurrent.futures.ThreadPoolExecutor(max_workers=c) as executor:
    for result in executor.map(lambda _:worker(),range(c)):rows+=result
  else:
   with concurrent.futures.ThreadPoolExecutor(max_workers=c) as executor:rows=list(executor.map(lambda i:one(ids[i%len(ids)],variant),range(count)))
  end=time.time();good=[r for r in rows if r['ok']];summary={'replicas':a.replicas,'phase':a.phase,'variant':variant,'source':source,'concurrency':c,'start_unix':start,'end_unix':end,'seconds':end-start,'requests':len(rows),'success':len(good),'images_per_second':len(good)/(end-start),'client_p50_ms':pct([r['client_ms'] for r in good],.5),'client_p95_ms':pct([r['client_ms'] for r in good],.95),'processing_p50_ms':pct([r['processing_ms'] for r in good],.5),'queue_p95_ms':pct([r['queue_ms'] for r in good],.95),'nodes':dict(collections.Counter(r['node'] for r in good)),'nonempty':sum(r['lines']>0 for r in good)}
  name=f"{a.phase}-r{a.replicas}-{variant}-{source}-c{c}";path=output/(name+'.json');path.write_text(json.dumps({'summary':summary,'requests':rows},indent=2)+'\n');print(json.dumps(summary),flush=True)
  if len(good)!=len(rows):raise RuntimeError('HTTP errors; inspect saved test results')
  if a.replicas>=2 and len(summary['nodes'])!=2:raise RuntimeError('Two-node test did not exercise both nodes')
 if a.phase=='sustain':run(a.variant,'mixed',a.concurrency,seconds=a.seconds)
 else:
  variants=['v4_legacy','v4_upright','v4_guarded','v6_small'] if a.replicas==1 else (['v4_upright','v4_guarded','v6_small'] if a.replicas==2 else ['v4_guarded','v6_small'])
  # Prewarm all selected engines, then allow downclock and measure a clean idle window.
  for variant in variants:
   for source in pool:one(pool[source][0],variant)
  time.sleep(10);idle_start=time.time();time.sleep(30);idle_end=time.time();(output/f'idle-r{a.replicas}.json').write_text(json.dumps({'start_unix':idle_start,'end_unix':idle_end,'replicas':a.replicas},indent=2)+'\n')
  for variant in variants:
   for source in ['doclaynet','rctw17']:
    for c in ([1,2,4,8] if a.replicas==1 else ([2,4,8] if a.replicas==2 else [4,8,16])):run(variant,source,c)
if __name__=='__main__':main()
