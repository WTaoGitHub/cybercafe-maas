"""Disposable Linux namespace regression; never touches host firewall or VMs.

Reproduces the legacy raw-hook reply drop, then checks post-conntrack fencing.
All listeners and packets are synthetic, with no credentials or model requests.
"""
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import uuid

sys.path[:0]=[str(Path(__file__).resolve().parents[3]),str(Path(__file__).resolve().parent)]
from maas_platform.boundary_policy import CHAIN,PORTS,REPLY_MATCH,rules
import boundary

SERVER="""import socket,threading
ports=PORTS
sockets=[]
def serve(s):
 while True:
  c,a=s.accept()
  def echo(c):
   try:
    while True:
     b=c.recv(256)
     if not b:break
     c.sendall(b)
   except OSError:pass
   finally:c.close()
  threading.Thread(target=echo,args=(c,),daemon=True).start()
for family in [socket.AF_INET,socket.AF_INET6]:
 for port in ports:
  s=socket.socket(family);s.setsockopt(socket.SOL_SOCKET,socket.SO_REUSEADDR,1)
  if family==socket.AF_INET6:s.setsockopt(socket.IPPROTO_IPV6,socket.IPV6_V6ONLY,1)
  s.bind(('0.0.0.0' if family==socket.AF_INET else '::',port));s.listen(32);sockets.append(s)
  threading.Thread(target=serve,args=(s,),daemon=True).start()
print('ready',flush=True)
threading.Event().wait()
"""
CLIENT="""import json,socket,sys,time
family=socket.AF_INET6 if ':' in sys.argv[1] else socket.AF_INET
s=socket.socket(family);s.settimeout(.5);t=time.monotonic()
try:
 if int(sys.argv[3]):s.bind(('::' if family==socket.AF_INET6 else '0.0.0.0',int(sys.argv[3])))
 s.connect((sys.argv[1],int(sys.argv[2])));s.sendall(b'probe');ok=s.recv(5)==b'probe';error=None
except OSError as e:ok=False;error=type(e).__name__
finally:s.close()
print(json.dumps({'ok':ok,'error_class':error,'seconds':time.monotonic()-t}))
"""


def main():
    if os.getuid()!=0:raise RuntimeError('Namespace test requires root')
    prefix='mgw-'+uuid.uuid4().hex[:7];left=prefix+'l';peer=prefix+'p';backend=prefix+'b'
    created=[];processes=[];host_rules={}
    def run(*args,input=None):
        return subprocess.run(args,input=input,text=True,capture_output=True,check=True,timeout=15).stdout
    def ns(name,*args,input=None):return run('ip','netns','exec',name,*args,input=input)
    def table(name,binary,which,lines):ns(name,binary+'-restore','--noflush',input='*'+which+'\n'+'\n'.join(lines)+'\nCOMMIT\n')
    report={'started_at':time.time(),'checks':[],'scope':'three disposable namespaces only; no host rules or VMs changed'}
    def effective(text):
        # Loading a netfilter module may add empty ACCEPT tables in every netns.
        # Compare all rules and non-ACCEPT policies, excluding this inert boilerplate.
        return [r for r in text.splitlines() if r.startswith('-A ') or
                (r.startswith(':') and ' ACCEPT ' not in r and ' - ' not in r)]
    def check(label,where,address,port,source,expected):
        value=json.loads(ns(where,'python3','-c',CLIENT,address,str(port),str(source)))
        report['checks'].append({'label':label,'expected':expected,**value})
        assert value['ok']==expected,label
    try:
        for binary in ['iptables','ip6tables']:
            host_rules[binary]=run(binary+'-save')
        for name in [left,peer,backend]:
            run('ip','netns','add',name);created.append(name);ns(name,'ip','link','set','lo','up')
        for a,b,ai,bi in [(left,peer,'lan','lan'),(left,backend,'br-test','eth0')]:
            x='x'+uuid.uuid4().hex[:8];y='y'+uuid.uuid4().hex[:8]
            run('ip','link','add',x,'type','veth','peer','name',y)
            run('ip','link','set',x,'netns',a);run('ip','link','set',y,'netns',b)
            ns(a,'ip','link','set',x,'name',ai);ns(b,'ip','link','set',y,'name',bi)
            ns(a,'ip','link','set',ai,'up');ns(b,'ip','link','set',bi,'up')
        for name,iface,address in [(left,'lan','198.18.246.1/30'),(peer,'lan','198.18.246.2/30'),(left,'br-test','172.29.247.1/30'),(backend,'eth0','172.29.247.2/30')]:ns(name,'ip','addr','add',address,'dev',iface)
        for name,address in [(left,'fd42:246::1/64'),(peer,'fd42:246::2/64')]:ns(name,'ip','-6','addr','add',address,'dev','lan','nodad')
        ns(peer,'ip','route','add','default','via','198.18.246.1');ns(backend,'ip','route','add','default','via','172.29.247.1')
        ns(left,'sysctl','-q','-w','net.ipv4.ip_forward=1')
        for name,ports in [(left,[22,40020]),(peer,[10150]),(backend,[8820])]:
            p=subprocess.Popen(['ip','netns','exec',name,'python3','-u','-c',SERVER.replace('PORTS',repr(ports))],stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
            processes.append(p)
            assert p.stdout.readline().strip()=='ready','Synthetic listener startup failed'
        ns(left,'iptables','-t','nat','-A','PREROUTING','-d','198.18.246.1','-p','tcp','--dport','18080','-j','DNAT','--to-destination','172.29.247.2:8820')
        check('baseline-dnat',peer,'198.18.246.1',18080,39001,True)
        # Keep an ORIGINAL-direction established request open across the policy change.
        code="import socket,sys,json;s=socket.create_connection(('198.18.246.1',40020));s.settimeout(.5);s.sendall(b'before');assert s.recv(6)==b'before';print('ready',flush=True);sys.stdin.readline()\ntry:\n s.sendall(b'after');ok=s.recv(5)==b'after'\nexcept OSError:ok=False\nprint(json.dumps({'ok':ok}),flush=True)"
        existing=subprocess.Popen(['ip','netns','exec',peer,'python3','-u','-c',code],stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True);processes.append(existing)
        assert existing.stdout.readline().strip()=='ready'
        cfg={'interface':'lan','bridge_subnets':['172.29.247.0/30']}
        for binary,family in [('iptables',4),('ip6tables',6)]:
            legacy=[r for r in rules(cfg,family) if REPLY_MATCH not in r]
            table(left,binary,'raw',['-N '+CHAIN,*legacy,'-A PREROUTING -j '+CHAIN])
        check('legacy-ipv4-normal-reply',left,'198.18.246.2',10150,39002,True)
        check('legacy-ipv4-overlap-dropped',left,'198.18.246.2',10150,41501,False)
        check('legacy-ipv6-overlap-dropped',left,'fd42:246::2',10150,41502,False)
        for binary,family in [('iptables',4),('ip6tables',6)]:
            table(left,binary,'mangle',['-N '+CHAIN,*rules(cfg,family),'-A PREROUTING -j '+CHAIN])
            inv={'interface':'lan','trusted_bridge':[]}
            lines=['-N '+boundary.HOST]
            if family==4:lines+=['-N '+boundary.DOCKER]
            lines+=boundary.chain_rules(inv,family)
            lines+=['-A INPUT -p tcp -m multiport --dports '+boundary.PORTS+' -j '+boundary.HOST]
            if family==4:lines+=['-A FORWARD -p tcp -d 172.29.247.2 --dport 8820 -j '+boundary.DOCKER]
            table(left,binary,'filter',lines)
            ns(left,binary,'-t','raw','-D','PREROUTING','-j',CHAIN)
        for address,fam,source in [('198.18.246.2','ipv4',41503),('fd42:246::2','ipv6',41504)]:
            check('fixed-'+fam+'-overlap-reply',left,address,10150,source,True)
        check('fixed-ipv4-normal-reply',left,'198.18.246.2',10150,39003,True)
        check('fixed-ipv6-normal-reply',left,'fd42:246::2',10150,39004,True)
        check('untrusted-host-inbound-blocked',peer,'198.18.246.1',40020,39005,False)
        check('untrusted-ipv6-inbound-blocked',peer,'fd42:246::1',40020,39006,False)
        check('untrusted-published-dnat-blocked',peer,'198.18.246.1',18080,39007,False)
        check('untrusted-direct-container-blocked',peer,'172.29.247.2',8820,39008,False)
        check('source-service-port-not-trusted',peer,'198.18.246.1',40020,10151,False)
        check('ssh-port-unaffected',peer,'198.18.246.1',22,39009,True)
        existing.stdin.write('check\n');existing.stdin.flush()
        v=json.loads(existing.stdout.readline());assert not v['ok']
        report['checks'].append({'label':'existing-untrusted-original-flow-blocked','expected':False,**v})
        report['ok']=True
    except BaseException as exc:
        report['ok']=False;report['error_class']=type(exc).__name__
        report['error_label']=str(exc)[:160]
        raise
    finally:
        for p in processes:
            if p.poll() is None:p.terminate()
            p.wait(timeout=5)
        for name in reversed(created):run('ip','netns','del',name)
        report['host_firewall_unchanged']=all(effective(run(b+'-save'))==effective(s) for b,s in host_rules.items())
        report['namespaces_removed']=all(n not in run('ip','netns','list') for n in created)
        destination=Path(__file__).resolve().parents[4]/('kernel-'+prefix+'.json')
        destination.write_text(json.dumps(report,indent=2)+'\n');destination.chmod(0o600)
        print(json.dumps(report),flush=True)
        assert report['host_firewall_unchanged'] and report['namespaces_removed']


if __name__=='__main__':main()
