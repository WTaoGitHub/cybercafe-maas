"""Prepare cloned-image Docker GPU support on tower; do not restart anything."""
import hashlib,json,os,platform,subprocess,time
from pathlib import Path
ROOT=Path('/root/wtaobj/tower-container-toolkit');VERSION='1.20.1-1'
PACKAGES=('nvidia-container-toolkit','nvidia-container-toolkit-base','libnvidia-container-tools','libnvidia-container1')
def main():
    if platform.node()!='tower-mNST8iPdv' or os.geteuid()!=0:raise RuntimeError('Authorized tower root only')
    ROOT.mkdir(mode=0o700,parents=True,exist_ok=True)
    def run(args):return subprocess.check_output(args,text=True,stderr=subprocess.PIPE,timeout=30).strip()
    before_boot=Path('/proc/sys/kernel/random/boot_id').read_bytes()
    before_docker=run(['systemctl','show','docker.service','-p','MainPID','-p','ExecMainStartTimestampMonotonic'])
    before_driver=Path('/proc/driver/nvidia/version').read_text()
    daemon=Path('/etc/docker/daemon.json');old=daemon.read_bytes() if daemon.exists() else None
    if old is not None:
        p=ROOT/'daemon.before.json';p.write_bytes(old);p.chmod(0o600)
        previous=json.loads(old)
    else:previous={}
    args=['apt-get','-s','--no-install-recommends','install',*[p+'='+VERSION for p in PACKAGES]]
    sim=run(args);(ROOT/'simulation.log').write_text(sim)
    for line in sim.splitlines():
        if line.startswith('Remv '):raise RuntimeError('Unexpected package removal')
        if line.startswith('Inst ') and line.split()[1] not in PACKAGES:raise RuntimeError('Unexpected dependency change')
    policy=Path('/usr/sbin/policy-rc.d')
    if policy.exists() or policy.is_symlink():raise RuntimeError('Existing service policy; inspect before modifying')
    policy_text='#!/bin/sh\n# Temporary tower Container Toolkit installation policy.\nexit 101\n'
    policy.write_text(policy_text);policy.chmod(0o755)
    env={**os.environ,'DEBIAN_FRONTEND':'noninteractive','NEEDRESTART_MODE':'l'}
    try:
        with (ROOT/'installation.log').open('w') as f:
            result=subprocess.run(['apt-get','-y','--no-install-recommends','-o','Dpkg::Options::=--force-confold','install',*[p+'='+VERSION for p in PACKAGES]],env=env,stdout=f,stderr=subprocess.STDOUT)
        if result.returncode:raise RuntimeError('Package installation failed; inspect log')
    finally:
        if policy.read_text()==policy_text:policy.unlink()
    # Write configuration only. Docker reads it at a later administrator-controlled start.
    configure=run(['nvidia-ctk','runtime','configure','--runtime=docker'])
    value=json.loads(daemon.read_text());assert 'nvidia' in value.get('runtimes',{})
    for key,original in previous.items():
        if key=='runtimes':
            for name,item in original.items():
                if name!='nvidia':assert value['runtimes'][name]==item
        else:assert value[key]==original
    report={'host':platform.node(),'checked_at':time.time(),'packages':{p:run(['dpkg-query','-W','-f=${Version}',p]) for p in PACKAGES},
        'nvidia_ctk_version':run(['nvidia-ctk','--version']),'runtime_version':run(['nvidia-container-runtime','--version']),
        'docker_runtime_configuration_written':True,'runtime_names':list(value.get('runtimes',{})),
        'default_runtime':value.get('default-runtime','runc'),'old_configuration_preserved':True,
        'docker_process_unchanged':run(['systemctl','show','docker.service','-p','MainPID','-p','ExecMainStartTimestampMonotonic'])==before_docker,
        'boot_unchanged':Path('/proc/sys/kernel/random/boot_id').read_bytes()==before_boot,
        'loaded_driver_unchanged':Path('/proc/driver/nvidia/version').read_text()==before_driver,
        'loaded_driver':before_driver.splitlines()[0],'dpkg_audit':run(['dpkg','--audit']),
        'temporary_policy_removed':not policy.exists(),'gpu_container_test_executed':False,
        'activation':'Pending administrator driver activation and Docker restart/start; no activation performed'}
    assert report['docker_process_unchanged'] and report['boot_unchanged'] and report['loaded_driver_unchanged'] and not report['dpkg_audit']
    p=ROOT/'verification.json';p.write_text(json.dumps(report,indent=2)+'\n');p.chmod(0o600)
    print(json.dumps(report))
if __name__=='__main__':main()
