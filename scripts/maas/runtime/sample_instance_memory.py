"""Read-only, finite worker-side sampler. Never a daemon or inference gate."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import time
sys.path.insert(0,str(Path(__file__).resolve().parents[3]))
from maas_bench.instance_memory import compute_apps,container_id


def command(args):
    return subprocess.check_output(args,text=True,stderr=subprocess.PIPE,timeout=10)


def sample(expected_uuid,cache):
    raw=command(['nvidia-smi','--query-gpu=uuid,memory.total,memory.used','--format=csv,noheader,nounits'])
    cards=[line.split(',') for line in raw.splitlines() if line.strip()]
    if len(cards)!=1 or cards[0][0].strip()!=expected_uuid:raise ValueError('Single expected GPU required')
    rows=compute_apps(command(['nvidia-smi','--query-compute-apps=gpu_uuid,pid,used_gpu_memory','--format=csv,noheader,nounits']),expected_uuid)
    processes=[];races=0
    for row in rows:
        try:
            proc=Path('/proc')/str(row['pid']);cid=container_id((proc/'cgroup').read_text())
            if cid not in cache:
                # Query only name; never read container env/credentials.
                name=command(['docker','inspect','--format','{{.Name}}',cid]).strip().lstrip('/')
                if '-run-' not in name:raise ValueError('Unmapped or foreign GPU workload')
                cache[cid]=name
            status={x.split(':',1)[0]:x.split(':',1)[1].strip() for x in (proc/'status').read_text().splitlines() if ':' in x}
            stat=(proc/'stat').read_text().rsplit(')',1)[1].split()
            processes.append({**row,'container_id':cid,'container_name':cache[cid],
                'rss_bytes':int(status['VmRSS'].split()[0])*1024,
                'lifetime_peak_rss_bytes':int(status['VmHWM'].split()[0])*1024,
                'process_start_ticks':int(stat[19]),'cpu_ticks':int(stat[11])+int(stat[12])})
        except FileNotFoundError:races+=1
    return {'kind':'instance_memory','at_epoch':time.time(),'at_monotonic':time.monotonic(),
        'sampler_pid':os.getpid(),'gpu_uuid':expected_uuid,'card_total_bytes':int(cards[0][1].strip())*1024**2,
        'card_used_bytes':int(cards[0][2].strip())*1024**2,'processes':processes,'process_exit_races':races}


def run(expected_uuid,seconds,interval,stop_file=None):
    if not expected_uuid.startswith('GPU-') or not 1<=seconds<=1200 or not .5<=interval<=5:
        raise ValueError('Explicit GPU and bounded diagnostic interval required')
    stop=Path(stop_file) if stop_file else None
    until=time.monotonic()+seconds;cache={}
    while time.monotonic()<until and not (stop and stop.exists()):
        start=time.monotonic();print(json.dumps(sample(expected_uuid,cache)),flush=True)
        time.sleep(max(0,interval-(time.monotonic()-start)))


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--expected-gpu-uuid',required=True);p.add_argument('--seconds',type=int,default=900);p.add_argument('--interval',type=float,default=1);p.add_argument('--stop-file');p.add_argument('--execute',action='store_true');a=p.parse_args()
    if a.execute:run(a.expected_gpu_uuid,a.seconds,a.interval,a.stop_file)
    else:print(json.dumps({'state':'DRY_RUN','execution':False}))
