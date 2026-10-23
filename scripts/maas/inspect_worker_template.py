#!/usr/bin/env python3
"""Read-only template inventory. No installs, service changes, secret reads or GPU workload."""
import json,platform,subprocess
from pathlib import Path

WORKER_IMAGE='quay.io/gpustack/gpustack@sha256:55ecc762950a32641b6a4d96195c0662ef72998eaeeda31bba0e8e218d41afef'


def command(args):
    try:
        p=subprocess.run(args,capture_output=True,text=True,timeout=15)
        return p.returncode==0,p.stdout.strip()
    except (OSError,subprocess.TimeoutExpired):return False,''


def collect():
    os_info={}
    p=Path('/etc/os-release')
    if p.exists():
        for line in p.read_text().splitlines():
            if '=' in line:
                k,v=line.split('=',1)
                if k in ('ID','VERSION_ID'):os_info[k]=v.strip('"')
    try:
        import cryptography
        crypto=cryptography.__version__
    except ImportError:crypto=None
    docker_ok,docker_version=command(['docker','version','--format','{{.Server.Version}}'])
    _,runtimes=command(['docker','info','--format','{{range $k,$v := .Runtimes}}{{$k}} {{end}}'])
    ids_ok,ids=command(['docker','ps','-aq'])
    image_ok,_=command(['docker','image','inspect',WORKER_IMAGE,'--format','{{json .RepoDigests}}'])
    gpu_ok,gpu=command(['nvidia-smi','--query-gpu=driver_version','--format=csv,noheader'])
    toolkit_ok,toolkit=command(['nvidia-ctk','--version'])
    # Installed service states only, not potentially sensitive environment/journal contents.
    units={name:command(['systemctl','is-enabled',name])[1] for name in (
        'cybercafe-provision.service','cybercafe-agent.service','maas-worker-bootstrap.service',
        'swcfghostname.service','gpustack.service','gpustack-worker.service')}
    machine=Path('/etc/machine-id')
    machine_id_initialized=machine.exists() and machine.read_text().strip() not in ('','uninitialized')
    state=[]
    for name in ('/var/lib/maas-bootstrap','/var/lib/gpustack','/root/.gpustack','/etc/gpustack'):
        p=Path(name)
        if p.exists() and (not p.is_dir() or any(p.iterdir())):state.append(name)
    blockers=[]
    if machine_id_initialized:blockers.append('OS_IDENTITY_GENERALIZATION_REQUIRED')
    host_key_count=len(list(Path('/etc/ssh').glob('ssh_host_*_key')))
    if host_key_count:blockers.append('SSH_HOST_KEY_GENERALIZATION_REQUIRED')
    if any(units[n] in ('enabled','enabled-runtime','static','linked','linked-runtime') for n in (
        'cybercafe-provision.service','cybercafe-agent.service','gpustack.service','gpustack-worker.service')):
        blockers.append('EXISTING_BOOT_OWNER_REVIEW_REQUIRED')
    if not ids_ok or ids.split():blockers.append('DOCKER_INSTANCES_REQUIRE_REVIEW')
    if state:blockers.append('INSTANCE_STATE_PRESENT')
    if not image_ok:blockers.append('PINNED_WORKER_IMAGE_NOT_CACHED')
    if not docker_ok or 'nvidia' not in runtimes.split() or not toolkit_ok or not gpu_ok:
        blockers.append('GPU_RUNTIME_PREREQUISITES_UNCONFIRMED')
    if os_info!={'ID':'ubuntu','VERSION_ID':'24.04'} or platform.machine()!='x86_64' or crypto!='41.0.7':
        blockers.append('BASE_DEPENDENCIES_REQUIRE_REVIEW')
    return {'schema_version':1,'readonly':True,'hostname':platform.node(),'os':os_info,
            'architecture':platform.machine(),'python':platform.python_version(),'cryptography':crypto,
            'docker_version':docker_version if docker_ok else None,'docker_runtimes':runtimes.split(),
            'container_count':len(ids.split()) if ids_ok else None,'driver_versions':gpu.splitlines() if gpu_ok else [],
            'container_toolkit':toolkit.splitlines()[0] if toolkit_ok else None,'units':units,
            'machine_id_initialized':machine_id_initialized,'ssh_host_key_count':host_key_count,
            'instance_state_paths':state,'pinned_worker_image':WORKER_IMAGE,'pinned_image_cached':image_ok,
            'sealing_review_items':blockers,'snapshot_ready':False,
            'limits':['no container GPU test','no boot or clone test','not a complete secret/startup-hook scan',
                      'snapshot_ready requires separate installation, generalization and administrator approval']}


if __name__=='__main__':print(json.dumps(collect(),indent=2))
