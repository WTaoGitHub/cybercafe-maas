"""Post-deployment ingress regression from one disposable, untrusted namespace.

Does not change firewall rules, existing containers, inference replicas or VMs.
Only synthetic listeners, its own namespace/veth and TCP handshakes are used.
"""
import json
import os
import re
from pathlib import Path
import subprocess
import time
import uuid


def main():
    if os.getuid()!=0:raise RuntimeError('Root required for the disposable namespace')
    prefix='mgwp'+uuid.uuid4().hex[:6];ns=prefix;link=prefix+'h';peer=prefix+'p'
    created=False;listener=None
    def run(*args):return subprocess.run(args,capture_output=True,text=True,check=True,timeout=15).stdout
    def probe(address,port):
        code="import socket,json,sys\ntry:\n s=socket.create_connection((sys.argv[1],int(sys.argv[2])),timeout=.5);s.close();ok=True\nexcept OSError:ok=False\nprint(json.dumps(ok))"
        return json.loads(run('ip','netns','exec',ns,'python3','-c',code,address,str(port)))
    def normalized(text):return [re.sub(r'\[\d+:\d+\]', '[COUNTERS]', r) for r in text.splitlines() if r and not r.startswith('#')]
    rules={b:normalized(run(b+'-save')) for b in ('iptables','ip6tables')}
    report={'started_at':time.time(),'checks':[],'original_flows_not_globally_trusted':True}
    try:
        run('ip','netns','add',ns);created=True
        run('ip','link','add',link,'type','veth','peer','name',peer);run('ip','link','set',peer,'netns',ns)
        run('ip','addr','add','198.18.245.1/30','dev',link)
        run('ip','-6','addr','add','fd42:245::1/64','dev',link,'nodad');run('ip','link','set',link,'up')
        for args in [('ip','addr','add','198.18.245.2/30','dev',peer),('ip','-6','addr','add','fd42:245::2/64','dev',peer,'nodad'),('ip','link','set',peer,'up'),('ip','link','set','lo','up'),('ip','route','add','default','via','198.18.245.1')]:run('ip','netns','exec',ns,*args)
        code="import socket,threading\nsockets=[]\nfor f,a in [(socket.AF_INET,'0.0.0.0'),(socket.AF_INET6,'::')]:\n s=socket.socket(f);s.setsockopt(socket.SOL_SOCKET,socket.SO_REUSEADDR,1)\n if f==socket.AF_INET6:s.setsockopt(socket.IPPROTO_IPV6,socket.IPV6_V6ONLY,1)\n s.bind((a,40062));s.listen();sockets.append(s)\nprint('ready',flush=True)\nthreading.Event().wait()"
        listener=subprocess.Popen(['python3','-u','-c',code],stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
        assert listener.stdout.readline().strip()=='ready'
        targets=[('198.18.245.1',40062),('fd42:245::1',40062),('10.11.31.1',18080),('10.11.31.1',18830),('10.11.31.1',18835),('10.11.31.1',18840),('172.17.0.2',80)]
        for address,port in targets:
            value=probe(address,port);report['checks'].append({'target':address,'port':port,'reachable':value});assert not value,(address,port)
        report['ssh_reachable']=probe('10.11.31.1',22);assert report['ssh_reachable']
        report['ok']=True
    finally:
        if listener:listener.terminate();listener.wait(timeout=5)
        if created:run('ip','netns','del',ns)
        subprocess.run(['ip','link','del',link],capture_output=True,timeout=5)
        report['namespace_removed']=ns not in run('ip','netns','list');assert report['namespace_removed']
        report['host_firewall_unchanged']=all(normalized(run(b+'-save'))==v for b,v in rules.items());assert report['host_firewall_unchanged']
    print(json.dumps(report))


if __name__=='__main__':main()
