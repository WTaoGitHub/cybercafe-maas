"""Run read-only on each worker AFTER stopping workloads; outputs no env/secrets.

This is an observation, not a poweroff authorization. It never stops containers,
changes model replicas, changes firewall rules or releases UNKNOWN records.
"""
import csv,io,json,socket,subprocess,time


def run(args):
    return subprocess.check_output(args,text=True,stderr=subprocess.PIPE,timeout=10)


def inspect(runner=run):
    ids=runner(['docker','ps','-q']).split()
    containers=json.loads(runner(['docker','inspect',*ids])) if ids else []
    workers=[];workloads=[]
    for c in containers:
        name=c['Name'].lstrip('/');config=c.get('Config',{});host=c.get('HostConfig',{})
        gpu=any('gpu' in cap for r in host.get('DeviceRequests') or [] for cap in r.get('Capabilities') or [])
        if 'gpustack' in name.lower() and 'worker' in name.lower():workers.append(name)
        # Include paused run containers and standalone GPU containers. Pause
        # placeholders alone are not compute processes.
        if '-run-' in name or gpu or config.get('Runtime')=='nvidia' or host.get('Runtime')=='nvidia':workloads.append(name)
    raw=runner(['nvidia-smi','--query-compute-apps=pid,process_name,used_gpu_memory','--format=csv,noheader,nounits'])
    processes=[]
    for row in csv.reader(io.StringIO(raw)):
        if not row:continue
        if len(row)!=3 or not row[0].strip().isdigit():raise RuntimeError('GPU process inventory cannot be verified')
        processes.append({'pid':int(row[0]),'name':row[1].strip(),'memory_mib':row[2].strip()})
    return {'worker':socket.gethostname(),'observed_at':time.time(),'running_worker_containers':workers,
        'running_workload_containers':workloads,'gpu_compute_processes':processes,
        'backend_stop_observed':not(workers or workloads or processes),
        'safe_to_poweroff':False,'scope':'Docker workloads and NVIDIA compute processes only; host workers, external admission and control plane require separate verification'}


if __name__=='__main__':
    try:print(json.dumps(inspect()))
    except Exception:raise SystemExit('Stop inspection failed; do not treat unavailable inventory as idle')
