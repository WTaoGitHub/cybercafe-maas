"""Own post-conntrack/pre-DNAT chain protects startup and outbound reply traffic."""
import argparse,fcntl,ipaddress,json,os,re,socket,subprocess,sys
from pathlib import Path
ROOT=Path('/root/wtaobj/maas-framework');sys.path.insert(0,str(ROOT))
from maas_platform.boundary_policy import CHAIN,TABLE,rules
from maas_platform.container_ingress import container_http_clients
import boundary
CFG=Path('/root/wtaobj/maas-boundary/persistent.json')


def run(*args,check=True,input=None,timeout=25):return subprocess.run(args,input=input,text=True,capture_output=True,check=check,timeout=timeout)


def resolve_container_clients(cfg, docker_ready):
    """Resolve only the interface of explicitly approved network names."""
    container_http_clients(cfg)
    clients, unavailable = [], []
    for row in cfg.get('container_http_clients', []):
        if 'network_name' not in row:
            clients.append(row)
            continue
        if not docker_ready:
            continue
        name = row['network_name']
        if not isinstance(name, str) or not re.fullmatch(r'[a-zA-Z0-9][a-zA-Z0-9_.-]*', name):
            raise ValueError('Invalid container HTTP network name')
        try:
            network, = json.loads(run('docker', 'network', 'inspect', name, timeout=2).stdout)
            if network['Name'] != name or network['Driver'] != 'bridge' or network['Scope'] != 'local':
                raise ValueError('Expected approved local Docker bridge')
            ipv4, = (entry for entry in network['IPAM']['Config'] if ':' not in entry['Subnet'])
            if (ipv4['Subnet'], ipv4['Gateway']) != (row['source_cidr'], row['listen_address']):
                raise ValueError('Docker subnet or gateway changed')
            interface = network['Options'].get('com.docker.network.bridge.name', 'br-' + network['Id'][:12])
            resolved = {**row, 'interface': interface}
            container_http_clients({**cfg, 'container_http_clients': [resolved]})
            link, = json.loads(run('ip', '-j', '-d', 'address', 'show', 'dev', interface, timeout=2).stdout)
            prefix = ipaddress.IPv4Network(row['source_cidr']).prefixlen
            if link['ifname'] != interface or link['linkinfo']['info_kind'] != 'bridge' or not any(
                addr['family'] == 'inet' and addr['local'] == row['listen_address']
                and addr['prefixlen'] == prefix
                for addr in link['addr_info']
            ):
                raise ValueError('Approved gateway is not installed on the Docker bridge')
        except (subprocess.SubprocessError, OSError, ValueError, KeyError, TypeError):
            unavailable.append(name)
            continue
        clients.append(resolved)
    return {**cfg, 'container_http_clients': clients}, unavailable
def apply_raw(cfg,trust):
    # Keep the callable name for existing ingress tools. Conntrack is unavailable
    # at the raw hook; mangle PREROUTING runs after it and before destination NAT.
    for family,binary in ((4,'iptables'),(6,'ip6tables')):
        if run(binary,'-w','3','-t',TABLE,'-S',CHAIN,check=False).returncode:run(binary,'-w','3','-t',TABLE,'-N',CHAIN)
        # Atomic table transaction: never flush global rules or expose an empty chain.
        text='*'+TABLE+'\n-F '+CHAIN+'\n'+'\n'.join(rules(cfg,family,trust))+'\nCOMMIT\n'
        run(binary+'-restore','-w','3','--noflush',input=text)
        listing=run(binary,'-t',TABLE,'-S','PREROUTING').stdout
        entries=[x for x in listing.splitlines() if x.startswith('-A ')]
        hook='-A PREROUTING -j '+CHAIN
        if not entries or entries[0]!=hook:run(binary,'-w','3','-t',TABLE,'-I','PREROUTING','1','-j',CHAIN)
        entries=[x for x in run(binary,'-t',TABLE,'-S','PREROUTING').stdout.splitlines() if x.startswith('-A ')]
        for index in range(len(entries),1,-1):
            if entries[index-1]==hook:run(binary,'-w','3','-t',TABLE,'-D','PREROUTING',str(index))
        assert run(binary,'-w','3','-t',TABLE,'-C','PREROUTING','-j',CHAIN).returncode==0
        # Install the effective replacement before removing only our legacy hook.
        while run(binary,'-w','3','-t','raw','-C','PREROUTING','-j',CHAIN,check=False).returncode==0:
            run(binary,'-w','3','-t','raw','-D','PREROUTING','-j',CHAIN)

def main():
    p=argparse.ArgumentParser();p.add_argument('action',choices=['install','boot','refresh','status']);a=p.parse_args()
    if socket.gethostname() not in boundary.NODES or os.getuid()!=0:raise RuntimeError('Existing root nodes only')
    fd=os.open('/run/maas-persistent-boundary.lock',os.O_CREAT|os.O_RDWR,0o600)
    with os.fdopen(fd,'w') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX)
        if a.action=='install':
            if CFG.exists():raise RuntimeError('Persistent policy already configured')
            inv=boundary.inventory();network=json.loads(run('docker','network','inspect','bridge').stdout)[0]
            cidrs=[str(ipaddress.IPv4Network(x['Subnet'])) for x in network['IPAM']['Config'] if ':' not in x['Subnet']]
            server=run('docker','inspect','wta-gpustack-server',check=False)
            image=json.loads(server.stdout)[0]['Image'] if server.returncode==0 else None
            cfg={'node':socket.gethostname(),'interface':inv['interface'],'bridge_subnets':cidrs,'server_image_id':image}
            handle=os.open(CFG,os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600)
            with os.fdopen(handle,'w') as f:json.dump(cfg,f)
        cfg=json.loads(CFG.read_text());assert cfg['node']==socket.gethostname()
        if a.action=='status':
            verdict={}
            for binary in ('iptables','ip6tables'):
                entries=[x for x in run(binary,'-t',TABLE,'-S','PREROUTING').stdout.splitlines() if x.startswith('-A ')]
                legacy=run(binary,'-t','raw','-C','PREROUTING','-j',CHAIN,check=False).returncode==0
                verdict[binary]=bool(entries and entries[0]=='-A PREROUTING -j '+CHAIN and not legacy)
            print(json.dumps({'early_table':TABLE,'early_hooks_first':verdict,'ok':all(verdict.values())}));return
        cfg, unavailable_clients = resolve_container_clients(cfg, a.action in ('refresh','install'))
        trust=[]
        if a.action in ('refresh','install'):
            inv=boundary.inventory()
            if cfg['server_image_id']:
                c=json.loads(run('docker','inspect','wta-gpustack-server').stdout)[0]
                if c['Image']!=cfg['server_image_id']:raise RuntimeError('Server image changed; early fence stays closed')
                for n in c['NetworkSettings']['Networks'].values():
                    if n.get('GlobalIPv6Address'):raise RuntimeError('Docker IPv6 needs explicit policy review')
                    if n.get('IPAddress'):trust.append(str(ipaddress.IPv4Address(n['IPAddress'])))
            # P4 raw fence remains closed while refreshing stale P3 runtime hooks.
            old=json.loads(boundary.STATE.read_text())
            if not old.get('active') or inv!=old.get('inventory') or not boundary.status()['rules_ok']:
                apply_raw(cfg,[])
                boundary.restore();boundary.apply()
                status=boundary.status()
                if not status['rules_ok'] or not status['inventory_matches']:raise RuntimeError('Runtime policy not verified')
                state=json.loads(boundary.STATE.read_text());boundary.run(['systemctl','stop',state['timer']+'.timer'],False);state['confirmed']=True;boundary.write(state)
        apply_raw(cfg,trust)
        print(json.dumps({'action':a.action,'node':cfg['node'],'pre_dnat_fence':True,'early_table':TABLE,
                          'trusted_bridge_count':len(trust),'unavailable_container_http_networks':unavailable_clients}))


if __name__=='__main__':main()
