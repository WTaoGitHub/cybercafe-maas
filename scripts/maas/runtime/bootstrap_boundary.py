"""Own INPUT chain for the bootstrap HTTPS port; no change to existing inference fences."""
import argparse, ipaddress, json, os, socket, subprocess
from pathlib import Path
CHAIN='MAAS-E5-CONFIG'

def run(*args,check=True,input=None):
    return subprocess.run(args,text=True,capture_output=True,check=check,input=input,timeout=15)

def render(policy, family):
    port=int(policy['port']);assert port==18840
    iface=policy['interface'];assert iface.replace('-','').replace('.','').isalnum()
    lines=[f'-A {CHAIN} -i lo -j ACCEPT']
    for raw in policy['allowed_cidrs']:
        net=ipaddress.ip_network(raw,strict=True)
        if net.version==family and not net.is_loopback:
            if net.prefixlen==0:raise ValueError('Unrestricted network is forbidden')
            lines.append(f'-A {CHAIN} -i {iface} -s {net} -j ACCEPT')
    lines.append(f'-A {CHAIN} -j DROP')
    return lines

def main():
    p=argparse.ArgumentParser();p.add_argument('--policy',required=True);a=p.parse_args()
    if os.getuid()!=0 or socket.gethostname()!='XZ-31-001':raise RuntimeError('001 root only')
    cfg=json.loads(Path(a.policy).read_text());assert cfg['bind']=='10.11.31.1'
    for family,binary in ((4,'iptables'),(6,'ip6tables')):
        lines=render(cfg,family)
        if run(binary,'-w','5','-t','filter','-S',CHAIN,check=False).returncode:
            run(binary,'-w','5','-t','filter','-N',CHAIN)
        # Only flush/replace our chain, atomically. Never flush a table or the host INPUT chain.
        restore='*filter\n-F '+CHAIN+'\n'+'\n'.join(lines)+'\nCOMMIT\n'
        run(binary+'-restore','-w','5','--noflush',input=restore)
        hook=['INPUT','-p','tcp','--dport',str(cfg['port']),'-j',CHAIN]
        if run(binary,'-w','5','-C',*hook,check=False).returncode:
            run(binary,'-w','5','-I','INPUT','1',*hook[1:])
    print(json.dumps({'configured':True,'port':cfg['port'],'allowed_cidrs':cfg['allowed_cidrs'],'interface':cfg['interface']}))
if __name__=='__main__':main()
