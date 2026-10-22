"""Quiesced live task-data backup. Host-only, no deletion of live volumes.
Archive contains private credentials: transfer encrypted and remove staging after restore.
"""
import argparse,hashlib,json,os,shutil,subprocess,time,urllib.request,uuid
from pathlib import Path
ROOT=Path('/root/wtaobj/maas-framework');LIVE=Path('/root/wtaobj/maas-live')
COMPOSE=['docker','compose','--env-file',str(LIVE/'app.env'),'-f',str(ROOT/'deploy/maas/compose.yaml')]


def run(*args,timeout=120):return subprocess.check_output(args,text=True,stderr=subprocess.PIPE,timeout=timeout)
def cli(*args):return json.loads(run(*COMPOSE,'exec','-T','api','python','-m','maas_platform.cli',*args))
def sha(path):
 h=hashlib.sha256()
 with Path(path).open('rb') as f:
  for b in iter(lambda:f.read(1024**2),b''):h.update(b)
 return h.hexdigest()
def write(path,value):
 fd=os.open(path,os.O_CREAT|os.O_TRUNC|os.O_WRONLY,0o600)
 with os.fdopen(fd,'w') as f:json.dump(value,f,indent=2);f.flush();os.fsync(f.fileno())
def ready():
 end=time.monotonic()+90
 while time.monotonic()<end:
  try:
   with urllib.request.urlopen('http://127.0.0.1:18830/healthz',timeout=2) as r:
    if r.status==200:return
  except Exception:pass
  time.sleep(1)
 raise RuntimeError('Live API not ready; preserve maintenance pause')


def main():
 p=argparse.ArgumentParser();p.add_argument('action',choices=['create','recover']);p.add_argument('--session');a=p.parse_args()
 if a.action=='recover':
  run(*COMPOSE,'start','api','executor','observer');return  # watchdog never resumes admission
 folder=LIVE/'backups'/('snapshot-'+uuid.uuid4().hex[:10]);folder.mkdir(mode=0o700,parents=True)
 report={'started_at':time.time(),'scope':'task DB + artifact + application config; not GPUStack DR'};write(folder/'status.json',report)
 status=cli('drain-status')
 if status['paused'] or status['active_tasks'] or status['backend_activity'] or status['sync_activity']:raise RuntimeError('Backup requires idle unpaused baseline')
 watchdog='maas-backup-recover-'+uuid.uuid4().hex[:8]
 run('systemd-run','--quiet','--unit',watchdog,'--on-active=180s','/usr/bin/python3',str(Path(__file__).resolve()),'recover')
 generation=None
 try:
  paused=cli('pause','--reason','P4 coherent task-data backup');generation=paused['generation'];assert paused['managed_requests_drained']
  run(*COMPOSE,'stop','--timeout','30','api','executor','observer')
  dbid=run(*COMPOSE,'ps','-q','db').strip();apiid=run(*COMPOSE,'ps','-aq','api').strip()
  c=json.loads(run('docker','inspect',apiid))[0];source=next(m['Source'] for m in c['Mounts'] if m['Destination']=='/var/lib/maas/artifacts')
  with (folder/'database.dump').open('wb') as f:subprocess.run(['docker','exec',dbid,'pg_dump','-h','127.0.0.1','-p','18835','-U','maas','-Fc','maas'],stdout=f,stderr=subprocess.PIPE,check=True,timeout=120)
  (folder/'database.dump').chmod(0o600)
  run('tar','-cf',str(folder/'artifacts.tar'),'-C',source,'.')
  run('tar','-cf',str(folder/'config.tar'),'-C',str(LIVE),'app.env','catalog.json','secrets','probes','identity.json','maintenance')
  for name in ('artifacts.tar','config.tar'):(folder/name).chmod(0o600)
  report.update(snapshot_at=time.time(),generation=generation,files={n:{'sha256':sha(folder/n),'bytes':(folder/n).stat().st_size} for n in ('database.dump','artifacts.tar','config.tar')})
  write(folder/'manifest.json',report)
  run('tar','-cf',str(folder/'snapshot.tar'),'-C',str(folder),'database.dump','artifacts.tar','config.tar','manifest.json')
  (folder/'snapshot.tar').chmod(0o600);report['archive_sha256']=sha(folder/'snapshot.tar');report['archive_bytes']=(folder/'snapshot.tar').stat().st_size
  report['ok']=True
 finally:
  run(*COMPOSE,'start','api','executor','observer');ready()
  current=cli('drain-status')
  if generation is not None and current['generation']==generation and current['managed_requests_drained']:
   report['resume']=cli('resume','--credential-id','pilot-ocr','--probe-image','/run/maas-probe/upright.png')
  else:report['resume_refused']=True
  run('systemctl','stop',watchdog+'.timer');report['finished_at']=time.time();write(folder/'status.json',report)
 print(json.dumps({'ok':report.get('ok',False),'folder':str(folder),'archive_sha256':report.get('archive_sha256'),'resume_refused':report.get('resume_refused',False)}))


if __name__=='__main__':main()
