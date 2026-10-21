"""Existing persistent policy: non-Docker disposable network client, post-only probes."""
import json,socket,subprocess,re
from pathlib import Path
import boundary
NS='maas-p4-client';HOST='maasp4host';PEER='maasp4peer'

def run(*a):return subprocess.check_output(a,text=True,stderr=subprocess.PIPE,timeout=20)
def probe(host,port):
 code="import socket,sys,json\ntry:\n s=socket.create_connection((sys.argv[1],int(sys.argv[2])),timeout=.5);s.close();ok=True\nexcept OSError:ok=False\nprint(json.dumps(ok))"
 return json.loads(run('ip','netns','exec',NS,'python3','-c',code,host,str(port)))
def main():
 if NS in [x.split()[0] for x in run('ip','netns','list').splitlines()]:raise RuntimeError('Test namespace exists')
 inv=boundary.inventory();created=False;listener=None;report={'node':inv['node']}
 ports=sorted({int(p) for p in re.findall(r':(400\d\d)\s',run('ss','-H','-ltn')) if 40000<=int(p)<=40063});assert ports
 report['listening_model_ports']=ports
 try:
  run('ip','netns','add',NS);created=True;run('ip','link','add',HOST,'type','veth','peer','name',PEER)
  run('ip','link','set',PEER,'netns',NS);run('ip','addr','add','10.254.224.1/30','dev',HOST)
  run('ip','-6','addr','add','fd42:224::1/64','dev',HOST,'nodad');run('ip','link','set',HOST,'up')
  run('ip','netns','exec',NS,'ip','addr','add','10.254.224.2/30','dev',PEER)
  run('ip','netns','exec',NS,'ip','-6','addr','add','fd42:224::2/64','dev',PEER,'nodad')
  run('ip','netns','exec',NS,'ip','link','set',PEER,'up');run('ip','netns','exec',NS,'ip','link','set','lo','up')
  run('ip','netns','exec',NS,'ip','route','add','default','via','10.254.224.1')
  listener=subprocess.Popen(['python3','-u','-c',"import socket;s=socket.socket(socket.AF_INET6);s.setsockopt(socket.IPPROTO_IPV6,socket.IPV6_V6ONLY,1);s.bind(('::',40062));s.listen();print('ready',flush=True)\nwhile True:\n c,a=s.accept();c.close()"],stdout=subprocess.PIPE,text=True);assert listener.stdout.readline().strip()=='ready'
  targets=[(inv['ip'],p) for p in (18080,8820,*ports,18830,18835)]+[('fd42:224::1',40062)]+[(x['ip'],x['port']) for x in inv['destinations']]
  report['probes']=[{'host':h,'port':p,'reachable':probe(h,p)} for h,p in targets]
  assert all(not x['reachable'] for x in report['probes'])
  report['ssh_reachable']=probe(inv['ip'],22);assert report['ssh_reachable'];report['ok']=True
 finally:
  if listener:listener.terminate();listener.wait(5)
  if created:subprocess.run(['ip','netns','del',NS],capture_output=True)
  subprocess.run(['ip','link','del',HOST],capture_output=True)
 Path('/root/wtaobj/maas-boundary/p4-probes.json').write_text(json.dumps(report,indent=2)+'\n');print(json.dumps(report))
if __name__=='__main__':main()
