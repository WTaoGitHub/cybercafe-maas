import ipaddress,json,re,subprocess,time
from pathlib import Path

def run(*args):return subprocess.check_output(args,text=True,stderr=subprocess.PIPE)
network=json.loads(run('docker','network','inspect','bridge'))[0]
assert network['Options']['com.docker.network.bridge.name']=='docker0'
assert subprocess.run(['ip','link','show','docker0'],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL).returncode!=0
ipam=network['IPAM']['Config'][0];subnet=ipaddress.ip_network(ipam['Subnet']);gateway=ipaddress.ip_address(ipam['Gateway'])
assert str(subnet)=='172.17.0.0/16' and str(gateway)=='172.17.0.1'
hostlinks={x['ifindex']:x['ifname'] for x in json.loads(run('ip','-j','link','show'))}
ports=[];macs=set()
for ident in network['Containers']:
    pid=int(run('docker','inspect','--format','{{.State.Pid}}',ident))
    if not pid:continue
    link=json.loads(run('nsenter','-t',str(pid),'-n','ip','-j','link','show','eth0'))[0]
    peer=hostlinks[link['link_index']]
    assert peer.startswith('veth')
    ports.append({'container':ident,'peer':peer})
    for neighbor in json.loads(run('nsenter','-t',str(pid),'-n','ip','-j','neigh','show','to',str(gateway))):
        if neighbor.get('lladdr'):macs.add(neighbor['lladdr'])
assert ports and len(macs)<=1
run('ip','link','add','docker0','type','bridge')
if macs:
    mac=next(iter(macs));assert re.fullmatch(r'[0-9a-f:]{17}',mac)
    run('ip','link','set','docker0','address',mac)
run('ip','addr','add',str(gateway)+'/'+str(subnet.prefixlen),'dev','docker0')
run('ip','link','set','docker0','up')
for item in ports:run('ip','link','set',item['peer'],'master','docker0')
evidence={'restored_at':time.time(),'subnet':str(subnet),'gateway':str(gateway),'ports_reattached':len(ports),'prior_mac_reused':bool(macs),'network_id':network['Id']}
Path('/root/wtaobj/maas-runtime/bridge-recovery.json').write_text(json.dumps(evidence,indent=2))
print(json.dumps(evidence))
