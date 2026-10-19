"""Host-side board power sampling; never changes GPU power settings."""
import argparse,json,subprocess,time
from pathlib import Path
p=argparse.ArgumentParser();p.add_argument('--output',type=Path,required=True);p.add_argument('--stop-file',type=Path,required=True);p.add_argument('--duration',type=float,default=3600);a=p.parse_args()
a.output.parent.mkdir(parents=True,exist_ok=True);start=time.monotonic()
rapl=list(Path('/sys/class/powercap').glob('intel-rapl:*/energy_uj'))
with a.output.open('w',buffering=1) as f:
 while not a.stop_file.exists() and time.monotonic()-start<a.duration:
  before=time.time()
  try:
   out=subprocess.check_output(['nvidia-smi','--query-gpu=uuid,power.draw,power.limit,utilization.gpu,memory.used','--format=csv,noheader,nounits'],text=True,timeout=5).strip().split(',')
   after=time.time();row={'unix_time':(before+after)/2,'query_ms':(after-before)*1000,'gpu_uuid':out[0].strip(),'power_w':float(out[1]),'power_limit_w':float(out[2]),'gpu_util_pct':float(out[3]),'memory_mib':float(out[4]),'cpu_energy_uj':{str(path):int(path.read_text()) for path in rapl if path.exists()}}
  except Exception as e:row={'unix_time':time.time(),'error':str(e)}
  f.write(json.dumps(row)+'\n');time.sleep(max(0,.5-(time.time()-before)))
