import json,subprocess
from pathlib import Path
out={}
def run(*a):
 try:return subprocess.run(a,capture_output=True,text=True,timeout=10).stdout.strip()
 except Exception:return None
out['virt_detection_output']=run('systemd-detect-virt')
out['related_modules']=[x.split()[0] for x in Path('/proc/modules').read_text().splitlines() if any(k in x.split()[0].lower() for k in ('sw','iscsi','nbd','virtio','xen','vmw'))]
out['block_links']={name:str((Path('/sys/class/block')/name).resolve()) for name in ('swblock0','swwbblock0','swblock_sysroot') if (Path('/sys/class/block')/name).exists()}
out['related_service_names']=[x.split()[0] for x in (run('systemctl','list-units','--all','--type=service','--no-legend','--plain') or '').splitlines() if any(k in x.split()[0].lower() for k in ('sw','cloud','disk','boot','net'))]
out['network_methods']=[]
for line in (run('nmcli','-t','-f','UUID,DEVICE','connection','show','--active') or '').splitlines():
 parts=line.split(':')
 if len(parts)==2 and parts[1] in ('enp2s0','enp3s0'):
  out['network_methods'].append({'device':parts[1],'method':run('nmcli','-g','ipv4.method,ipv4.addresses,ipv4.gateway','connection','show','uuid',parts[0])})
out['vendor_services']={name:run('systemctl','show',name,'-p','ActiveState','-p','SubState','-p','Description') for name in ('swnetboot_success.service','swcfghostname.service')}
out['netplan_dhcp_flags']={}
for p in Path('/etc/netplan').glob('*.yaml'):
 out['netplan_dhcp_flags'][p.name]=[x.strip() for x in p.read_text().splitlines() if x.strip().startswith(('dhcp4:','dhcp6:','renderer:'))]
# Do not inspect connection payloads, process arguments, device mapper tables or secrets.
print(json.dumps(out,indent=2))
