"""Controller-only API rollout. No database schema, worker, VM or listener changes."""
import argparse,hashlib,json,os,socket,subprocess,sys,time,urllib.request
from pathlib import Path
ROOT=Path('/root/wtaobj/maas-consumer-ocr-r1')
FRAME=Path('/root/wtaobj/maas-framework')
LIVE=Path('/root/wtaobj/maas-live')
STATE=ROOT/'rollout.private.json'
sys.path[:0]=[str(FRAME/'scripts/maas/runtime'),str(FRAME),'/root/wtaobj']
from e5_ocr_pilot import cli
IMAGE='cybercafe-maas-tasks:consumer-ocr-r1'
OTHERS=['maas-tasks-executor-1','maas-tasks-observer-1','maas-tasks-db-1','wta-gpustack-server','maas-bootstrap-config','maas-registry-store','maas-registry-tls']

def run(args,timeout=45):
 p=subprocess.run(args,text=True,capture_output=True,timeout=timeout)
 if p.returncode:raise RuntimeError('Operation failed: '+args[0]+'; inspect private rollout state')
 return p.stdout.strip()

def save(v):
 with STATE.open('w') as f:os.fchmod(f.fileno(),0o600);json.dump(v,f,indent=2);f.flush();os.fsync(f.fileno())

def inspect(name):return json.loads(run(['docker','inspect',name]))[0]

def inplace(path,raw):
 with path.open('wb') as f:f.write(raw);f.flush();os.fsync(f.fileno())

def prepare():
 assert not STATE.exists()
 status=cli('status');drain=cli('drain-status')
 assert not drain['paused'] and not any(drain[k] for k in ['active_tasks','backend_activity','sync_activity'])
 assert not status['tasks'].get('QUEUED',0) and not status['unknown_backend_activity'] and not status['unknown_sync_activity']
 api=inspect('maas-tasks-api-1');assert api['Image']=='sha256:82c4588ac9e82704a3bbc5aeb21f890d76a5d3a0d02af963291b6193e85d15d7'
 run(['docker','image','inspect',IMAGE])
 # Refuse a schema change in this API-only rollout.
 for path in (ROOT/'source/maas_platform/migrations').glob('*.sql'):
  assert path.read_bytes()==(FRAME/'maas_platform/migrations'/path.name).read_bytes()
 backup=ROOT/'backup';backup.mkdir(mode=0o700)
 files=[LIVE/'app.env',Path('/root/wtaobj/maas-edge/nginx.conf'),Path('/root/wtaobj/maas-private-tls/nginx.conf')]
 files += [FRAME/'deploy/maas/compose.yaml',FRAME/'deploy/maas/edge.nginx.conf']
 files += [FRAME/'maas_platform'/n for n in ['api.py','sync_ingress.py','limits.py']]
 saved={}
 for index,p in enumerate(files):
  dest=backup/str(index);dest.write_bytes(p.read_bytes());dest.chmod(0o600);saved[str(p)]=str(dest)
 data={'phase':'PREPARED','old_api':api,'files':saved,'others':{n:{'id':inspect(n)['Id'],'started':inspect(n)['State']['StartedAt']} for n in OTHERS},
       'boot_id':Path('/proc/sys/kernel/random/boot_id').read_text().strip(),'docker_pid':run(['systemctl','show','docker','-p','MainPID','--value'])}
 save(data)
 paused=cli('pause','--reason','Deploy consumer-compatible synchronous OCR')
 data.update(phase='PAUSED',generation=paused['generation']);save(data)
 assert cli('drain-status')['managed_requests_drained']
 print(json.dumps({'phase':'PAUSED','generation':data['generation']}))

def install():
 s=json.loads(STATE.read_text());assert s['phase']=='PAUSED'
 d=cli('drain-status');assert d['paused'] and d['generation']==s['generation'] and d['managed_requests_drained']
 hashes=json.loads((ROOT/'source-hashes.json').read_text())
 for name,digest in hashes.items():assert hashlib.sha256((ROOT/'source'/name).read_bytes()).hexdigest()==digest
 for name in hashes:
  if name.startswith('maas_platform/') or name.startswith('deploy/maas/') or name.startswith('requirements-'):
   target=FRAME/name;target.parent.mkdir(parents=True,exist_ok=True)
   inplace(target,(ROOT/'source'/name).read_bytes())
 env=(LIVE/'app.env').read_text();assert not any(line.startswith('MAAS_API_IMAGE=') for line in env.splitlines())
 inplace(LIVE/'app.env',(env.rstrip()+'\nMAAS_API_IMAGE='+IMAGE+'\n').encode())
 command=['docker','compose','--env-file',str(LIVE/'app.env'),'-f',str(FRAME/'deploy/maas/compose.yaml')]
 run(command+['config','--quiet'])
 s['phase']='API_RECREATING';save(s)
 run(command+['up','-d','--no-deps','--no-build','--pull','never','--force-recreate','api'],timeout=120)
 for _ in range(30):
  try:
   with urllib.request.urlopen('http://127.0.0.1:18830/healthz',timeout=2) as r:
    if r.status==200:break
  except OSError:time.sleep(1)
 else:raise RuntimeError('API unhealthy; keep maintenance paused')
 s['phase']='API_UPDATED';save(s)
 for name in ['maas-edge','maas-private-tls']:
  p=Path('/root/wtaobj')/name/'nginx.conf';raw=p.read_text()
  assert '/ocr 1;' not in raw and '/v1/ocr 1;' not in raw
  assert '        /v1/chat/completions 1;' in raw
  inplace(p,raw.replace('        /v1/chat/completions 1;','        /v1/chat/completions 1;\n        /ocr 1;\n        /v1/ocr 1;').encode())
  run(['docker','exec',name,'nginx','-t'])
  run(['docker','exec',name,'nginx','-s','reload'])
 s['phase']='EDGE_UPDATED';save(s)
 new=inspect('maas-tasks-api-1');assert new['Config']['Image']==IMAGE
 assert all(inspect(n)['Id']==old['id'] and inspect(n)['State']['StartedAt']==old['started'] for n,old in s['others'].items())
 assert s['boot_id']==Path('/proc/sys/kernel/random/boot_id').read_text().strip() and s['docker_pid']==run(['systemctl','show','docker','-p','MainPID','--value'])
 print(json.dumps({'phase':s['phase'],'new_api_id':new['Id'],'new_image_id':new['Image'],'other_components_unchanged':True,'generation':s['generation']}))

def resume():
 s=json.loads(STATE.read_text());assert s['phase']=='EDGE_UPDATED'
 d=cli('drain-status');assert d['paused'] and d['generation']==s['generation'] and d['managed_requests_drained']
 result=cli('resume','--credential-id','pilot-ocr','--probe-image','/run/maas-probe/upright.png')
 assert not result['maintenance']['paused']
 s.update(phase='RESUMED',resume_probe=result['probes']);save(s)
 new=inspect('maas-tasks-api-1')
 public={'phase':s['phase'],'image':IMAGE,'image_id':new['Image'],'api_container_id':new['Id'],'resume_probe':result['probes'],
         'other_components_unchanged':True,'vm_boot_unchanged':True,'docker_pid_unchanged':True,'database_schema_changed':False,'public_listener_added':False}
 (ROOT/'deployment.json').write_text(json.dumps(public,indent=2));print(json.dumps(public))

if __name__=='__main__':
 assert os.getuid()==0 and socket.gethostname()=='XZ-31-001'
 p=argparse.ArgumentParser();p.add_argument('phase',choices=['prepare','install','resume']);a=p.parse_args();globals()[a.phase]()
