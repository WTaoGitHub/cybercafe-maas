"""Read-only one-shot VM evidence. No credential contents, raw hardware IDs or mutation."""
import hashlib,json,os,platform,subprocess,time
from pathlib import Path

def run(*args):
    try:
        p=subprocess.run(args,capture_output=True,text=True,timeout=15)
        return p.stdout.strip() if p.returncode==0 else None
    except (OSError,subprocess.TimeoutExpired):return None

def hashed(value):return hashlib.sha256(value.strip().encode()).hexdigest() if value and value.strip() else None

def read(path):
    try:return Path(path).read_text().strip()
    except OSError:return None

def command_json(*args):
    try:return json.loads(run(*args) or 'null')
    except ValueError:return None

def main():
    out={'observed_at':time.time(),'hostname':platform.node(),'kernel':platform.release(),'architecture':platform.machine(),
         'virtualization':run('systemd-detect-virt'),'uptime_seconds':float((read('/proc/uptime') or '0').split()[0])}
    out['os_release']={k:v.strip('"') for line in (read('/etc/os-release') or '').splitlines() if '=' in line for k,v in [line.split('=',1)] if k in ('NAME','VERSION_ID','PRETTY_NAME')}
    out['dmi']={name:read('/sys/class/dmi/id/'+name) for name in ('sys_vendor','product_name','product_version','board_vendor','bios_vendor','bios_version')}
    out['identity_sha256']={name:hashed(read(path)) for name,path in {'machine_id':'/etc/machine-id','boot_id':'/proc/sys/kernel/random/boot_id','dmi_product_uuid':'/sys/class/dmi/id/product_uuid','dmi_product_serial':'/sys/class/dmi/id/product_serial'}.items()}
    out['block_devices']=command_json('lsblk','-J','-o','NAME,TYPE,SIZE,FSTYPE,MOUNTPOINTS,MODEL,RO,TRAN')
    out['root_mount']=command_json('findmnt','-J','--target','/','-o','TARGET,SOURCE,FSTYPE')
    out['wtaobj_mount']=command_json('findmnt','-J','--target','/root/wtaobj','-o','TARGET,SOURCE,FSTYPE')
    out['network_mount_types']=sorted({r.split()[2] for r in (read('/proc/mounts') or '').splitlines() if len(r.split())>2 and r.split()[2] in ('nfs','nfs4','cifs','9p','virtiofs','overlay','tmpfs')})
    out['boot']={'efi':Path('/sys/firmware/efi').exists(),'cmdline_flags':[x for x in (read('/proc/cmdline') or '').split() if x in ('ro','rw','boot=casper','boot=live','netboot=nfs','rd.neednet=1')],
        'root_argument_kind':next(('UUID' if x.startswith('root=UUID=') else 'PARTUUID' if x.startswith('root=PARTUUID=') else 'device' if x.startswith('root=/dev/') else 'other' for x in (read('/proc/cmdline') or '').split() if x.startswith('root=')),None)}
    out['cloud_init']={'installed':Path('/usr/bin/cloud-init').exists(),'status':run('cloud-init','status'),
        'datasource':read('/var/lib/cloud/instance/datasource'),'instance_id_sha256':hashed(read('/var/lib/cloud/data/instance-id'))}
    out['network']={'addresses':command_json('ip','-j','address','show'),'routes':command_json('ip','-j','route','show','default')}
    # MACs are evidence identifiers, not authentication; hash before saving.
    for iface in out['network']['addresses'] or []:
        if 'address' in iface:iface['mac_sha256']=hashed(iface.pop('address'))
        iface.pop('broadcast',None)
    out['network_config_files']=[str(p) for folder in ('/etc/netplan','/etc/systemd/network') for p in Path(folder).glob('*') if p.is_file()]
    out['dhcp_lease_files']=[p.name for p in Path('/run/systemd/netif/leases').glob('*') if p.is_file()]
    out['gpu']=run('nvidia-smi','--query-gpu=name,memory.total,driver_version,pci.bus_id','--format=csv,noheader')
    ids=run('nvidia-smi','--query-gpu=uuid','--format=csv,noheader')
    out['gpu_uuid_sha256']=[hashed(x) for x in ids.splitlines()] if ids else []
    out['cpu']={'logical_count':os.cpu_count(),'model':next((x.split(':',1)[1].strip() for x in (read('/proc/cpuinfo') or '').splitlines() if x.startswith('model name')),None)}
    out['memory_total_kib']=next((int(x.split()[1]) for x in (read('/proc/meminfo') or '').splitlines() if x.startswith('MemTotal:')),None)
    out['gpustack']={}
    for name in ('wta-gpustack-worker','wta-gpustack-server'):
        rows=command_json('docker','inspect',name)
        if not rows:continue
        c=rows[0];mounts=[{k:m.get(k) for k in ('Type','Source','Destination','RW','Name')} for m in c['Mounts']]
        row={'image':c['Config']['Image'],'running':c['State']['Running'],'started_at':c['State']['StartedAt'],'mounts':mounts,'configured':{}}
        allowed={'GPUSTACK_WORKER_NAME','GPUSTACK_WORKER_IP','GPUSTACK_DATA_DIR','GPUSTACK_WORKER_IFNAME','GPUSTACK_DISABLE_WORKER'}
        row['configured']={k:v for raw in c['Config'].get('Env',[]) if '=' in raw for k,v in [raw.split('=',1)] if k in allowed}
        # Only explicitly allowed flags; never include server URL/token/command lines.
        argv=(c['Config'].get('Entrypoint') or [])+(c['Config'].get('Cmd') or [])
        for i,x in enumerate(argv):
            if x in ('--worker-name','--worker-ip','--data-dir','--worker-ifname') and i+1<len(argv):row['configured'][x]=argv[i+1]
        for m in mounts:
            if m['Destination']!='/var/lib/gpustack':continue
            root=Path(m['Source']);row['data_mount']=command_json('findmnt','-J','--target',str(root),'-o','TARGET,SOURCE,FSTYPE')
            row['identity_files']={}
            for fn in ('worker_name','worker_uuid','worker_token','token','config.yaml'):
                p=root/fn
                if p.is_file():
                    row['identity_files'][fn]={'exists':True,'mode':oct(p.stat().st_mode&0o777),'bytes':p.stat().st_size}
                    if fn in ('worker_name','worker_uuid'):row['identity_files'][fn]['sha256']=hashed(read(p))
        out['gpustack'][name]=row
    print(json.dumps(out,ensure_ascii=False,indent=2))
if __name__=='__main__':main()
