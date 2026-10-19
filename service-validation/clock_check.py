"""Estimate worker clock offset from controller using read-only health probes."""
import argparse,json,time,urllib.request
from pathlib import Path
p=argparse.ArgumentParser();p.add_argument('--replicas',type=int,required=True);a=p.parse_args();root=Path('/root/wtaobj/service-validation');deployment=json.loads((root/f'deployment-{a.replicas}-replicas.json').read_text());checks=[]
for item in deployment['instances']:
 samples=[]
 for _ in range(3):
  before=time.time()
  with urllib.request.urlopen(f"http://{item['worker_ip']}:{item['port']}/health",timeout=10) as response:result=json.load(response)
  after=time.time();samples.append({'worker_minus_controller_s':result['unix_time']-(before+after)/2,'rtt_ms':1000*(after-before)})
 best=min(samples,key=lambda x:x['rtt_ms']);checks.append({'worker':item['worker_name'],'node':result['node'],**best})
assert all(abs(x['worker_minus_controller_s'])<.5 for x in checks),'Clock offset too large for unadjusted power integration'
(root/f'clocks-r{a.replicas}.json').write_text(json.dumps(checks,indent=2)+'\n');print(json.dumps(checks))
