"""Install exact R580 + CUDA13.0 on the explicitly named tower only; never reboot.

No driver module reload, kernel upgrade, full system upgrade or autoremove.
Logs and previous package state remain private on the target host.
"""
import hashlib,json,os,platform,subprocess,time,urllib.request
from pathlib import Path
HOST='tower-mNST8iPdv';ROOT=Path('/root/wtaobj/tower-cuda-upgrade')
DRIVER='580.95.05-0ubuntu1'

def main():
    if platform.node()!=HOST or os.geteuid()!=0:raise RuntimeError('Authorized tower root only')
    ROOT.mkdir(mode=0o700,parents=True,exist_ok=True)
    start_boot=Path('/proc/sys/kernel/random/boot_id').read_text().strip()
    report={'host':HOST,'started_at':time.time(),'requested_driver':DRIVER,'requested_toolkit':'13.0','reboot_executed':False,'module_reload_requested':False,'phase':'PREPARED'}
    env={**os.environ,'DEBIAN_FRONTEND':'noninteractive','NEEDRESTART_MODE':'l','UCF_FORCE_CONFFOLD':'1'}
    def save():
        p=ROOT/'installation.json';p.write_text(json.dumps(report,indent=2)+'\n');p.chmod(0o600)
    def run(name,args):
        report['phase']=name;save()
        with (ROOT/(name+'.log')).open('w') as f:
            code=subprocess.run(args,env=env,stdout=f,stderr=subprocess.STDOUT).returncode
        if code:raise RuntimeError(name+' failed; inspect private log')
    before=subprocess.check_output(['dpkg-query','-W','-f=${Package} ${Version} ${db:Status-Status}\n'],text=True)
    if not (ROOT/'packages-before.txt').exists():(ROOT/'packages-before.txt').write_text(before)
    report['running_driver_before']=Path('/proc/driver/nvidia/version').read_text().strip()
    report['toolkit_12_2_preserved']=Path('/usr/local/cuda-12.2/bin/nvcc').is_file()
    policy=Path('/usr/sbin/policy-rc.d');backup=ROOT/'policy-rc.d.before'
    had_policy=policy.exists() or policy.is_symlink()
    if had_policy:raise RuntimeError('Existing service policy requires explicit merge; no package changes made')
    policy.write_text('#!/bin/sh\n# Temporary tower package installation: defer service starts/restarts.\nexit 101\n');policy.chmod(0o755)
    try:
        # Official signed NVIDIA repository must be prepared and refreshed first.
        if not Path('/usr/share/keyrings/cuda-archive-keyring.gpg').exists():
            raise RuntimeError('Prepare official CUDA keyring/repository before installation')
        # Pin already installed driver branch/version despite NVIDIA repo priority.
        pref=Path('/etc/apt/preferences.d/tower-r580-5809505')
        pref.write_text('Package: nvidia-* libnvidia-* xserver-xorg-video-nvidia-*\nPin: version '+DRIVER+'\nPin-Priority: 1001\n')
        sim_driver=subprocess.check_output(['apt-get','-s','--no-install-recommends','install','nvidia-driver-580='+DRIVER,'libnvidia-compute-580='+DRIVER,'nvidia-utils-535-','nvidia-compute-utils-535-'],env=env,text=True,stderr=subprocess.STDOUT)
        (ROOT/'driver-simulation.log').write_text(sim_driver)
        allowed_removals=('nvidia-','libnvidia-','xserver-xorg-video-nvidia-')
        for line in sim_driver.splitlines():
            if line.startswith('Remv ') and not line.split()[1].startswith(allowed_removals):
                raise RuntimeError('Unexpected non-driver removal; inspect simulation')
        legacy=subprocess.run(['dpkg-query','-W','-f=${db:Status-Status}','nvidia-utils-535'],capture_output=True,text=True)
        if legacy.returncode==0 and legacy.stdout.strip()=='installed':
            # apt's transaction orders the missing compute library before this
            # old tool package; remove the now-unneeded owner first, never force overwrite.
            run('remove-legacy-utils',['dpkg','--remove','nvidia-utils-535'])
        run('driver-install',['apt-get','-y','--no-install-recommends','-o','Dpkg::Options::=--force-confold','install','nvidia-driver-580='+DRIVER,'libnvidia-compute-580='+DRIVER,'nvidia-utils-535-','nvidia-compute-utils-535-'])
        sim=subprocess.check_output(['apt-get','-s','--no-install-recommends','install','cuda-toolkit-13-0'],env=env,text=True,stderr=subprocess.STDOUT)
        (ROOT/'toolkit-simulation.log').write_text(sim)
        if any(line.startswith('Remv ') for line in sim.splitlines()):raise RuntimeError('Toolkit would remove packages; refusing')
        if any(line.startswith('Inst ') and ('nvidia-driver-' in line or 'linux-image-' in line) for line in sim.splitlines()):raise RuntimeError('Unexpected driver/kernel change in toolkit transaction')
        run('toolkit-install',['apt-get','-y','--no-install-recommends','-o','Dpkg::Options::=--force-confold','install','cuda-toolkit-13-0'])
        report['toolkit_nvcc']=subprocess.check_output(['/usr/local/cuda-13.0/bin/nvcc','--version'],text=True).strip()
        report['installed_driver']=subprocess.check_output(['dpkg-query','-W','-f=${Version}','nvidia-driver-580'],text=True).strip()
        report['running_driver_after']=Path('/proc/driver/nvidia/version').read_text().strip()
        report['dkms']=subprocess.check_output(['dkms','status'],text=True).strip()
        report['dpkg_audit']=subprocess.check_output(['dpkg','--audit'],text=True).strip()
        assert report['installed_driver']==DRIVER and not report['dpkg_audit']
        assert Path('/proc/sys/kernel/random/boot_id').read_text().strip()==start_boot
        assert Path('/usr/local/cuda-12.2/bin/nvcc').is_file()
        report['boot_id_unchanged']=True;report['phase']='INSTALLED_PENDING_ADMIN_ACTIVATION';report['finished_at']=time.time();save()
        print(json.dumps(report))
    except Exception as e:
        report['phase']='FAILED_REQUIRES_INSPECTION';report['error']=str(e);save();raise
    finally:
        # Remove only our own temporary policy; no service or driver restart here.
        if policy.read_text().startswith('#!/bin/sh\n# Temporary tower package installation:'):policy.unlink()
if __name__=='__main__':main()
