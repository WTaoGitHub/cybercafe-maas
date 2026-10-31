"""Finite diagnostic process samples and a conservative candidate, not a hard cap."""
import csv
import io
import math
import re

MIB=1024**2
GIB=1024**3


def compute_apps(text,expected_uuid):
    rows=[];seen=set()
    for row in csv.reader(io.StringIO(text)):
        if not row:continue
        if len(row)!=3:raise ValueError('Malformed GPU process sample')
        gpu,pid,memory=(x.strip() for x in row)
        if gpu!=expected_uuid or not pid.isdigit() or not memory.isdigit():
            raise ValueError('Unknown GPU/process memory; no resource budget')
        key=(gpu,int(pid))
        if key in seen:raise ValueError('Duplicate GPU process sample')
        seen.add(key);rows.append({'gpu_uuid':gpu,'pid':int(pid),'gpu_memory_bytes':int(memory)*MIB})
    return rows


def container_id(cgroup):
    ids=set(re.findall(r'(?<![a-f0-9])[a-f0-9]{64}(?![a-f0-9])',cgroup))
    if len(ids)!=1:raise ValueError('GPU process does not map to one container')
    return ids.pop()


def sampled_container_totals(processes):
    totals={}
    for p in processes:
        key=p['container_id'];value=p['gpu_memory_bytes']
        if not isinstance(key,str) or not key or type(value) is not int or value<0:
            raise ValueError('Explicit measured container memory required')
        totals[key]=totals.get(key,0)+value
    return totals


def candidate_claim(sampled_max_bytes):
    if type(sampled_max_bytes) is not int or sampled_max_bytes<=0:
        raise ValueError('Positive sampled per-container maximum required')
    # Additional 20% or 512MiB, whichever is greater, then 256MiB rounding.
    # A sampled maximum is still not a proven absolute peak.
    wanted=max(math.ceil(sampled_max_bytes*1.2),sampled_max_bytes+512*MIB)
    quantum=256*MIB
    return ((wanted+quantum-1)//quantum)*quantum


def process_memory(status):
    """Exiting tasks can remain in NVML after Linux removes RSS fields."""
    state=status.get('State','').split()
    if state and state[0] in ('Z','X','x'):
        return None
    result={}
    for source,target in (('VmRSS','rss_bytes'),('VmHWM','lifetime_peak_rss_bytes')):
        parts=status.get(source,'').split()
        if len(parts)!=2 or not parts[0].isdigit() or parts[1]!='kB':
            raise ValueError('Live GPU process has unavailable RSS evidence')
        result[target]=int(parts[0])*1024
    return result
