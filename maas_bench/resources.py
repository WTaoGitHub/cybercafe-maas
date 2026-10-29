"""Temporary experiment resource checks; never a production request gate."""
from pathlib import Path
import resource
import sys
import time


class ResourcePressure(RuntimeError):pass


class ResourceGuard:
    def __init__(self,workers):self.workers={w['id']:w for w in workers};self.high={};self.previous_cpu=None

    def check(self,rows,controller,at):
        readings=[('controller',controller['cpu_percent'],controller['available_ram_bytes'],controller['total_ram_bytes'])]
        for row in rows:
            w=self.workers[row['worker_id']];total=w['ram_total'];gpu_total=w['gpus'][0]['memory_total']
            if gpu_total-row['worker_node_gram_used_bytes']<max(1024**3,.1*gpu_total):raise ResourcePressure('GPU_HEADROOM')
            readings.append((str(w['id']),row['worker_node_cpu_utilization_rate'],total-row['worker_node_memory_used_bytes'],total))
        for key,cpu,available,total in readings:
            if available<max(2*1024**3,.1*total):raise ResourcePressure('RAM_HEADROOM')
            if cpu is not None and cpu>=80:
                if key not in self.high:self.high[key]=at
                if at-self.high[key]>=30:raise ResourcePressure('SUSTAINED_CPU')
            else:self.high.pop(key,None)

    def controller(self):
        cpu=[int(v) for v in Path('/proc/stat').read_text().splitlines()[0].split()[1:9]]
        total=sum(cpu);idle=cpu[3]+cpu[4];percent=None
        if self.previous_cpu:
            dt=total-self.previous_cpu[0];di=idle-self.previous_cpu[1]
            if dt>0:percent=100*(dt-di)/dt
        self.previous_cpu=(total,idle)
        mem={line.split(':')[0]:int(line.split()[1])*1024 for line in Path('/proc/meminfo').read_text().splitlines()}
        return {'cpu_percent':percent,'available_ram_bytes':mem['MemAvailable'],'total_ram_bytes':mem['MemTotal'],
            'process_peak_rss_bytes':resource.getrusage(resource.RUSAGE_SELF).ru_maxrss*(1 if sys.platform=='darwin' else 1024)}
