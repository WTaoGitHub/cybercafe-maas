"""Real IPv4/IPv6 host and Docker forwarding probes, in a disposable netns.

No Docker daemon/worker creation. Only own veth/netns/listener are removed.
Success confirms runtime policy; failure restores only our policy.
"""
import json,re,socket,subprocess,sys,time
from pathlib import Path
import boundary
NS='maas-p3-client';LINK='maasp3host';PEER='maasp3peer'


def run(*args):return subprocess.check_output(args,text=True,stderr=subprocess.PIPE,timeout=20)


def probe(target,port):
    code='import socket,sys,json\ntry:\n s=socket.create_connection((sys.argv[1],int(sys.argv[2])),timeout=1);s.close();ok=True\nexcept OSError:ok=False\nprint(json.dumps(ok))'
    return json.loads(run('ip','netns','exec',NS,'python3','-c',code,target,str(port)))


def main():
    if NS in [x.split()[0] for x in run('ip','netns','list').splitlines()]:raise RuntimeError('Test namespace already exists')
    if subprocess.run(['ip','link','show',LINK],capture_output=True).returncode==0:raise RuntimeError('Test link already exists')
    inv=boundary.inventory();report={'node':inv['node'],'no_new_worker_or_daemon':True};armed=False;listener=None;namespace=False
    try:
        listeners=run('ss','-H','-ltn')
        ports=sorted({int(p) for p in re.findall(r':(400\d\d)\s',listeners) if 40000<=int(p)<=40063})
        assert ports and 40062 not in ports
        targets=[{'kind':'model','host':inv['ip'],'port':p} for p in ports]
        targets.append({'kind':'legacy-dnat','host':inv['ip'],'port':8820})
        for d in inv['destinations']:
            targets.append({'kind':'direct-container','host':d['ip'],'port':d['port']})
            if d['name']=='wta-gpustack-server':targets.append({'kind':'server-dnat','host':inv['ip'],'port':18080})
        run('ip','netns','add',NS);namespace=True
        run('ip','link','add',LINK,'type','veth','peer','name',PEER)
        run('ip','link','set',PEER,'netns',NS)
        run('ip','addr','add','10.254.223.1/30','dev',LINK)
        run('ip','-6','addr','add','fd42:223::1/64','dev',LINK,'nodad')
        run('ip','link','set',LINK,'up')
        run('ip','netns','exec',NS,'ip','addr','add','10.254.223.2/30','dev',PEER)
        run('ip','netns','exec',NS,'ip','-6','addr','add','fd42:223::2/64','dev',PEER,'nodad')
        run('ip','netns','exec',NS,'ip','link','set',PEER,'up')
        run('ip','netns','exec',NS,'ip','link','set','lo','up')
        run('ip','netns','exec',NS,'ip','route','add','default','via','10.254.223.1')
        listener=subprocess.Popen(['python3','-u','-c',"import socket;s=socket.socket(socket.AF_INET6);s.setsockopt(socket.IPPROTO_IPV6,socket.IPV6_V6ONLY,1);s.bind(('::',40062));s.listen();print('ready',flush=True)\nwhile True:\n c,a=s.accept();c.close()"],stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
        assert listener.stdout.readline().strip()=='ready'
        targets.append({'kind':'ipv6-host','host':'fd42:223::1','port':40062})
        for t in targets:
            t['before']=probe(t['host'],t['port'])
            # Recent Docker versions already fence direct routed bridge IPs.
            # Record that existing protection instead of claiming a new block.
            if t['kind']!='direct-container':assert t['before'],t
        assert probe(inv['ip'],22)
        report['applied']=boundary.apply();armed=True
        for t in targets:
            t['after']=probe(t['host'],t['port']);assert not t['after'],t
        report['probes']=targets;report['ssh_reachable_after']=probe(inv['ip'],22);assert report['ssh_reachable_after']
        status=boundary.status();assert status['rules_ok'] and status['inventory_matches']
        # This is only a runtime validation; confirmation is deliberately a
        # separate operation after controller health and real OCR rechecks.
        report['ok']=True
    except BaseException:
        if armed:boundary.restore()
        raise
    finally:
        if listener is not None:listener.terminate();listener.wait(timeout=5)
        if namespace:subprocess.run(['ip','netns','del',NS],capture_output=True)
        subprocess.run(['ip','link','del',LINK],capture_output=True)
        report['test_namespace_removed']=NS not in [x.split()[0] for x in run('ip','netns','list').splitlines()]
        out=Path('/root/wtaobj/maas-boundary');out.mkdir(mode=0o700,exist_ok=True)
        (out/'acceptance.json').write_text(json.dumps(report,indent=2)+'\n')
    print(json.dumps(report))


if __name__=='__main__':main()
