"""Select lowest-resource setting within 95% of best mixed-workload estimate."""
import argparse,json,subprocess,sys
from pathlib import Path
p=argparse.ArgumentParser();p.add_argument('--variant',required=True);p.add_argument('--replicas',type=int);p.add_argument('--seconds',type=int,default=120);p.add_argument('--run',action='store_true');a=p.parse_args();root=Path('/root/wtaobj/service-validation');groups={}
for path in (root/'load-results').glob('matrix-*.json'):
 s=json.loads(path.read_text())['summary']
 if s['variant']!=a.variant or a.replicas and s['replicas']!=a.replicas:continue
 groups.setdefault((s['replicas'],s['concurrency']),{})[s['source']]=s
candidates=[]
for (r,c),ss in groups.items():
 if set(ss)!=set(['doclaynet','rctw17']):continue
 candidates.append({'replicas':r,'concurrency':c,'estimated_mixed_ips':2/(1/ss['doclaynet']['images_per_second']+1/ss['rctw17']['images_per_second'])})
peak=max(x['estimated_mixed_ips'] for x in candidates);selected=min((x for x in candidates if x['estimated_mixed_ips']>=.95*peak),key=lambda x:(x['replicas'],x['concurrency']))
result={'variant':a.variant,'selected':selected,'rule':'Harmonic mean for 1:1 request mix; choose lowest replicas/concurrency within95% of max estimate; validate actual throughput with sustained test. This is capacity selection, not a profit optimum.','candidates':candidates,'sustain_seconds':a.seconds};(root/f'selection-{a.variant}.json').write_text(json.dumps(result,indent=2)+'\n');print(json.dumps(result),flush=True)
if a.run:
 state=json.loads((root/'deployment.json').read_text());sys.path.insert(0,'/root/wtaobj');from gpustack_admin import api
 model=api('/v2/models/'+str(state['model_id']))
 if model['replicas']!=selected['replicas']:
  subprocess.check_call(['python3',str(root/'manage.py'),'scale',str(selected['replicas'])]);subprocess.check_call(['python3',str(root/'warm_workers.py'),'--replicas',str(selected['replicas'])])
 subprocess.check_call(['python3',str(root/'load.py'),'--replicas',str(selected['replicas']),'--phase','sustain','--variant',a.variant,'--concurrency',str(selected['concurrency']),'--seconds',str(a.seconds)])
