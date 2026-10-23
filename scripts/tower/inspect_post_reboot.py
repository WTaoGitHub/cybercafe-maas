"""One-shot read-only guest evidence. No services changed, secrets printed or metadata endpoints probed."""
import hashlib,json,os,platform,subprocess,time
from pathlib import Path

def read(p):
 try:return Path(p).read_text().strip()
 except OSError:return ''
def digest(v):return hashlib.sha256(v.encode()).hexdigest() if v else None
def run(*args):
 try:
  p=subprocess.run(args,text=True,capture_output=True,timeout=15)
  return {'code':p.returncode,'stdout':p.stdout.strip(),'stderr':p.stderr.strip()[:500]}
 except (OSError,subprocess.TimeoutExpired) as e:return {'error':type(e).__name__}
def main():
 r={'checked_at':time.time(),'host':platform.node(),'kernel':platform.release(),'boot_id_sha256':digest(read('/proc/sys/kernel/random/boot_id')),'uptime_seconds':read('/proc/uptime').split()[0]}
 r['identities']={k:digest(read(p)) for k,p in {'machine_id':'/etc/machine-id','dmi_uuid':'/sys/class/dmi/id/product_uuid','dmi_serial':'/sys/class/dmi/id/product_serial'}.items()}
 r['ssh_public_fingerprints']={p.name:run('ssh-keygen','-lf',str(p)) for p in Path('/etc/ssh').glob('ssh_host_*_key.pub')}
 r['authorized_keys_metadata']={'exists':Path('/root/.ssh/authorized_keys').exists()}
 if r['authorized_keys_metadata']['exists']:
  s=Path('/root/.ssh/authorized_keys').stat();r['authorized_keys_metadata'].update(bytes=s.st_size,mode=oct(s.st_mode&0o777))
 r['driver']=read('/proc/driver/nvidia/version');r['gpu']=run('nvidia-smi','--query-gpu=name,uuid,pci.bus_id,driver_version,memory.total','--format=csv,noheader')
 q=run('nvidia-smi','-q').get('stdout','');r['gpu_selected_details']=[x.strip() for x in q.splitlines() if any(t in x for t in ('Virtualization Mode','Host VGPU','vGPU Heterogeneous','MIG Mode','GPU UUID','Serial Number','Product Name'))]
 r['pci']=run('lspci','-nnk','-s','01:00.0');pci=Path('/sys/bus/pci/devices/0000:01:00.0')
 r['pci_attributes']={k:read(pci/k) for k in ('vendor','device','subsystem_vendor','subsystem_device','sriov_totalvfs','sriov_numvfs')}
 r['pci_links']={k:str((pci/k).resolve()) if (pci/k).exists() else None for k in ('driver','iommu_group','physfn')}
 r['mdev_bus_present']=Path('/sys/class/mdev_bus').exists();r['virt_detection']=run('systemd-detect-virt')
 r['cpu_hypervisor_flag']=any('hypervisor' in x.split() for x in read('/proc/cpuinfo').splitlines() if x.startswith('flags'))
 r['dmi']={k:read('/sys/class/dmi/id/'+k) for k in ('sys_vendor','product_name','board_name','bios_vendor')}
 r['root_mount']=run('findmnt','-n','-o','SOURCE,FSTYPE','/')
 r['modules']=[x.split()[0] for x in read('/proc/modules').splitlines() if any(t in x.split()[0].lower() for t in ('sw','virtio','xen','vmw','nvidia'))]
 r['cloud_init']={'disabled_marker':Path('/etc/cloud/cloud-init.disabled').exists(),'status':run('cloud-init','status'),'instance_id_sha256':digest(read('/var/lib/cloud/data/instance-id')),'datasource':read('/var/lib/cloud/instance/datasource')}
 r['injection_path_metadata']={}
 for name in ('/var/lib/cloud/seed','/run/cloud-init','/sys/firmware/qemu_fw_cfg','/dev/virtio-ports','/etc/rc.local','/etc/cybercafe','/run/credentials'):
  p=Path(name);r['injection_path_metadata'][name]={'exists':p.exists(),'entries':[x.name for x in p.iterdir()][:30] if p.is_dir() else []}
 r['vendor_units']={}
 for unit in ('swcfghostname.service','swnetboot_success.service','cloud-init-local.service','cloud-init.service','rc-local.service','docker.service','sshd.service'):
  r['vendor_units'][unit]=run('systemctl','show',unit,'-p','LoadState','-p','ActiveState','-p','SubState','-p','UnitFileState','-p','FragmentPath','-p','ExecMainStartTimestamp','-p','ExecMainExitTimestamp','-p','Result','-p','Before','-p','After')
 # Read only executable names, never arguments/environment from provider units.
 for unit in ('swcfghostname.service','swnetboot_success.service'):
  raw=run('systemctl','cat',unit).get('stdout','')
  r['vendor_units'][unit]['exec_paths']=[line.split('=',1)[1].split()[0] for line in raw.splitlines() if line.startswith(('ExecStart=','ExecStartPre='))]
 r['network']={'ipv4':run('ip','-j','-4','addr'),'default_route':run('ip','-j','route','show','default'),'dns':run('resolvectl','dns'),'active_methods':run('nmcli','-t','-f','NAME,DEVICE,TYPE','connection','show','--active')}
 r['packages']=run('dpkg-query','-W','-f=${Package} ${Version}\n','nvidia-driver-580','cuda-toolkit-13-0','nvidia-container-toolkit','nvidia-container-toolkit-base','libnvidia-container1','libnvidia-container-tools')
 r['nvcc']=run('/usr/local/cuda/bin/nvcc','--version');r['cuda_target']=str(Path('/usr/local/cuda').resolve());r['container_cli']=run('nvidia-container-cli','info')
 d=run('docker','info','--format','{{json .}}')
 if d.get('code')==0:
  j=json.loads(d['stdout']);r['docker']={'runtime_names':sorted(j.get('Runtimes',{})),'default_runtime':j.get('DefaultRuntime'),'server_version':j.get('ServerVersion')}
 r['images']=run('docker','image','ls','--format','{{.Repository}}:{{.Tag}}');r['containers']=run('docker','ps','-a','--format','{{.Names}} {{.Status}}');r['dpkg_audit']=run('dpkg','--audit')
 r['probe_binary_exists']=Path('/root/wtaobj/tower-cuda-upgrade/cuda13_probe').is_file()
 print(json.dumps(r,indent=2))
if __name__=='__main__':main()
