"""Run only inside STOPPED global maintenance. Restart the existing Docker engine.
Samples the pre-DNAT fence throughout, never creates a worker/daemon/network.
"""
import json,subprocess,threading,time
from pathlib import Path


def run(*a,timeout=90):return subprocess.check_output(a,text=True,stderr=subprocess.PIPE,timeout=timeout)
def main():
 state=json.loads(Path('/root/wtaobj/maas-node002-maintenance/state.json').read_text()) if Path('/root/wtaobj/maas-node002-maintenance/state.json').exists() else None
 if Path('/root/wtaobj/maas-live').exists():
  live=Path('/root/wtaobj/maas-live');active=json.loads((live/'maintenance/active.json').read_text());cfg=json.loads(Path(active['config']).read_text())
  assert json.loads(Path(cfg['journal']).read_text())['phase']=='STOPPED'
 else:assert state and state['active'] and state.get('stopped_at')
 oldpid=run('systemctl','show','docker.service','-p','MainPID','--value').strip();samples=[];stop=threading.Event()
 def sample():
  while not stop.is_set():
   ok=all(subprocess.run([b,'-w','2','-t','raw','-C','PREROUTING','-j','MAAS-P4-EARLY'],capture_output=True,timeout=4).returncode==0 for b in ('iptables','ip6tables'))
   samples.append(ok);stop.wait(.05)
 thread=threading.Thread(target=sample);thread.start();start=time.monotonic()
 try:run('systemctl','restart','docker.service',timeout=100)
 finally:stop.set();thread.join(10)
 newpid=run('systemctl','show','docker.service','-p','MainPID','--value').strip()
 assert oldpid!=newpid and newpid!='0' and samples and all(samples)
 run('systemctl','start','maas-boundary-refresh.service')
 result={'docker_pid_changed':True,'raw_fence_samples':len(samples),'missing_fence_samples':sum(not x for x in samples),'seconds':round(time.monotonic()-start,3),'host_rebooted':False}
 Path('/root/wtaobj/maas-boundary/docker-restart-proof.json').write_text(json.dumps(result,indent=2)+'\n');print(json.dumps(result))
if __name__=='__main__':main()
