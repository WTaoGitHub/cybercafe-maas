"""Temporary non-Docker network namespace negative probe; all resources cleaned up."""
import json,subprocess,time
from pathlib import Path
NS='maas-e5-client';HOST='maase5host';PEER='maase5peer'

def run(*args):return subprocess.check_output(args,text=True,stderr=subprocess.PIPE,timeout=15)
def drops():
    rows=run('iptables','-w','3','-nvx','-L','MAAS-E5-CONFIG').splitlines()
    return sum(int(x.split()[0]) for x in rows if len(x.split())>2 and x.split()[2]=='DROP')
def main():
    if NS in [x.split()[0] for x in run('ip','netns','list').splitlines()]:raise RuntimeError('Namespace already exists')
    # Capture other fences to ensure the test never changes them.
    fences={b:{table:run(b,'-t',table,'-S') for table in ('raw',)} for b in ('iptables','ip6tables')}
    created=False
    try:
        run('ip','netns','add',NS);created=True
        run('ip','link','add',HOST,'type','veth','peer','name',PEER)
        run('ip','link','set',PEER,'netns',NS)
        run('ip','addr','add','198.18.224.1/30','dev',HOST);run('ip','link','set',HOST,'up')
        run('ip','netns','exec',NS,'ip','addr','add','198.18.224.2/30','dev',PEER)
        run('ip','netns','exec',NS,'ip','link','set',PEER,'up');run('ip','netns','exec',NS,'ip','link','set','lo','up')
        run('ip','netns','exec',NS,'ip','route','add','default','via','198.18.224.1')
        before=drops()
        code="import socket,json\ntry:\n s=socket.create_connection(('10.11.31.1',18840),timeout=2);s.close();reachable=True\nexcept OSError:reachable=False\nprint(json.dumps({'reachable':reachable}))"
        r=json.loads(run('ip','netns','exec',NS,'python3','-c',code));after=drops()
        assert not r['reachable'] and after>before
        report={'checked_at':time.time(),'outside_source':'198.18.224.2','destination':'10.11.31.1:18840',
                'reachable':r['reachable'],'dedicated_drop_packets_increment':after-before,'non_docker_client':True}
    finally:
        if created:subprocess.run(['ip','netns','del',NS],capture_output=True,timeout=10)
        subprocess.run(['ip','link','del',HOST],capture_output=True,timeout=10)
    report['temporary_namespace_removed']=NS not in [x.split()[0] for x in run('ip','netns','list').splitlines()]
    report['existing_raw_fences_unchanged']=all(run(b,'-t',t,'-S')==v for b,ts in fences.items() for t,v in ts.items())
    assert report['temporary_namespace_removed'] and report['existing_raw_fences_unchanged']
    report['ok']=True
    Path('/root/wtaobj/maas-bootstrap/boundary-probe.json').write_text(json.dumps(report,indent=2)+'\n')
    print(json.dumps(report))
if __name__=='__main__':main()
