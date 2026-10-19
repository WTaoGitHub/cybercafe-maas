"""Prime every target instance directly before a steady-state gateway matrix."""
import argparse,base64,concurrent.futures,json,mimetypes,random,time,urllib.request
from pathlib import Path
p=argparse.ArgumentParser();p.add_argument('--replicas',type=int,required=True);a=p.parse_args();root=Path('/root/wtaobj/service-validation');data=root/'dataset';manifest={r['id']:r for r in map(json.loads,(data/'manifest.jsonl').read_text().splitlines())};split=json.loads((data/'splits.json').read_text())['evaluation'];ids=[]
for source in ['doclaynet','rctw17']:
 chosen=[x for x in split if manifest[x]['source']==source];random.Random(20261019).shuffle(chosen);ids+=chosen[:20]
payloads={i:'data:'+mimetypes.guess_type(manifest[i]['image'])[0]+';base64,'+base64.b64encode((data/manifest[i]['image']).read_bytes()).decode() for i in ids}
variants=['v4_upright','v4_guarded','v6_small'] if a.replicas==2 else ['v4_guarded','v6_small'];instances=json.loads((root/f'deployment-{a.replicas}-replicas.json').read_text())['instances']
def warm(instance):
 start=time.time();nodes=set();count=0
 for mode in variants:
  for ident in ids:
   body={'model':'wta-ocr-validation','stream':False,'ocr_variant':mode,'messages':[{'role':'user','content':[{'type':'text','text':'ocr_variant='+mode},{'type':'image_url','image_url':{'url':payloads[ident]}}]}]}
   req=urllib.request.Request(f"http://{instance['worker_ip']}:{instance['port']}/v1/chat/completions",data=json.dumps(body).encode(),headers={'Content-Type':'application/json'})
   with urllib.request.urlopen(req,timeout=120) as response:result=json.loads(json.load(response)['choices'][0]['message']['content'])
   assert result['variant']==mode;nodes.add(result['node']);count+=1
  print('Primed',instance['worker_name'],instance['port'],mode,flush=True)
 return {'instance_id':instance['id'],'worker':instance['worker_name'],'port':instance['port'],'requests':count,'seconds':time.time()-start,'nodes':list(nodes)}
with concurrent.futures.ThreadPoolExecutor(max_workers=len(instances)) as executor:results=list(executor.map(warm,instances))
(root/f'warm-r{a.replicas}.json').write_text(json.dumps(results,indent=2)+'\n')
