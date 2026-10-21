"""Local coordinator for Docker restart acceptance inside a bound maintenance window."""
import json,shlex,subprocess,time
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2]
FIRST=['ssh','-S',str(ROOT/'service-validation/ssh-001.sock'),'-o','ProxyCommand=false','root@10.11.31.1']
SECOND=['ssh','-p','16721','-i',str(Path.home()/'.ssh/id_ed25519'),'-o','BatchMode=yes','root@111.4.255.126']
REMOTE='/root/wtaobj/maas-framework';OUT=ROOT/'results/maas-p4-release';OUT.mkdir(parents=True,exist_ok=True)


def run(ssh,args,timeout=420):return subprocess.check_output(ssh+[shlex.join(args)],text=True,stderr=subprocess.PIPE,timeout=timeout)
def main():
 cfg=run(FIRST,['python3',REMOTE+'/scripts/maas/runtime/bind_maintenance.py','new-session']).strip()
 report={'config_path':cfg,'started_at':time.time()}
 def control(action):return json.loads(run(FIRST,['python3',REMOTE+'/scripts/maas/runtime/maintenance_control.py','--config',cfg,action]))
 def save():(OUT/'docker-restart-window.json').write_text(json.dumps(report,indent=2)+'\n')
 try:
  report['begin']=control('begin')['phase'];save();report['stop']=control('stop')['phase'];save()
  report['node002']=json.loads(run(SECOND,['python3',REMOTE+'/scripts/maas/runtime/restart_daemon_probe.py']));save()
  report['node001']=json.loads(run(FIRST,['python3',REMOTE+'/scripts/maas/runtime/restart_daemon_probe.py']));save()
  # Wait for the existing GPUStack server and live task DB/API after daemon restart.
  code="import time,urllib.request\nend=time.monotonic()+180\nwhile time.monotonic()<end:\n try:\n  a=urllib.request.urlopen('http://127.0.0.1:18080/version',timeout=2);b=urllib.request.urlopen('http://127.0.0.1:18830/healthz',timeout=2);assert a.status==b.status==200;break\n except Exception:time.sleep(1)\nelse:raise RuntimeError('Controller did not recover')"
  run(FIRST,['python3','-c',code],timeout=210)
  report['recover']=control('recover')['phase'];save();report['resume']=control('resume')['phase'];report['ok']=report['resume']=='RESUMED'
 finally:
  if report.get('resume')!='RESUMED':
   try:report['emergency_recover']=control('recover')['phase'];report['emergency_resume']=control('resume')['phase']
   except Exception:report['operator_recovery_required']=True
  report['finished_at']=time.time();save()
 print(json.dumps({k:report.get(k) for k in ('ok','resume','operator_recovery_required')}))
if __name__=='__main__':main()
