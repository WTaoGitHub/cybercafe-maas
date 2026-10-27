"""Template-only registry client installation; never apply, register or start containers.

Requires a verified Mac backup first. Reject baseline drift or instance state;
fetch and verify the protected release only in memory before changing files.
"""
import argparse,hashlib,json,os,socket,subprocess,sys,tempfile
from pathlib import Path
DEST=Path('/opt/maas/lib/maas_bootstrap')
STATE_FILES=['identity.json','applied.json','prepared.json','maintenance-backups.json','registry-auth','generations']

def run(args):return subprocess.check_output(args,text=True,stderr=subprocess.PIPE,timeout=20).strip()
def atomic(path,raw,mode):
 fd,tmp=tempfile.mkstemp(prefix='.registry-template-',dir=path.parent)
 try:
  with os.fdopen(fd,'wb') as f:os.fchmod(f.fileno(),mode);f.write(raw);f.flush();os.fsync(f.fileno())
  os.replace(tmp,path)
 finally:
  if os.path.exists(tmp):os.unlink(tmp)

def main(stage):
 host=socket.gethostname();assert host.lower()=='tower' or host.lower().startswith('tower-')
 assert os.getuid()==0
 manifest=json.loads((stage/'sources.json').read_text())
 boot=Path('/proc/sys/kernel/random/boot_id').read_text().strip();assert boot==manifest['boot_id']
 docker_pid=run(['systemctl','show','docker','-p','MainPID','--value'])
 assert not run(['docker','ps','-aq'])
 cfg=Path('/etc/maas/bootstrap.json');conf=json.loads(cfg.read_text());state=Path(conf['state_dir'])
 assert conf['image_id']=='tower-e5-pilot-v1' and conf['cluster_id']==3
 assert not any((state/n).exists() for n in STATE_FILES)
 assert not Path(conf['worker_data_dir']).exists() and not (DEST/'registry.py').exists()
 backup=Path('/root/wtaobj/e5-registry-template-install');assert not backup.exists()
 for n,h in manifest['before'].items():assert hashlib.sha256((DEST/n).read_bytes()).hexdigest()==h
 for n,h in manifest['after'].items():
  raw=(stage/'maas_bootstrap'/n).read_bytes();assert hashlib.sha256(raw).hexdigest()==h;compile(raw,str(DEST/n),'exec')
 # No worker/apply command. HTTPS requests never write a generation, token or auth file.
 sys.path.insert(0,str(stage))
 from maas_bootstrap.client import LocalSettings,HTTPSConfigFetcher,ReleaseVerifier,BootstrapError
 from maas_bootstrap.worker import guard_clone_host,NativeDockerWorker
 settings=LocalSettings(cfg);settings.data.update(image_id='tower-e5-registry-v1',allowed_registry_endpoints=['10.11.31.1:18443'])
 envelope=HTTPSConfigFetcher().fetch(settings);metadata=ReleaseVerifier().verify(settings,envelope)
 assert metadata['revision']=='e5-registry-r1'
 assert envelope['manifest']['registry_access']['endpoint']=='10.11.31.1:18443'
 assert envelope['registration']['registry_access']['username']=='maas-reader'
 spec=NativeDockerWorker().desired_layout(settings,envelope)
 assert spec['version']=='registry-access-v1' and '/run/maas-registry-auth' in spec['env']['GPUSTACK_RUNTIME_DEPLOY_MIRRORED_DEPLOYMENT_IGNORE_VOLUMES'].split(';')
 try:guard_clone_host()
 except BootstrapError as e:assert e.code=='PROTECTED_HOST_WORKER_FORBIDDEN'
 else:raise AssertionError('Template guard failed')
 backup.mkdir(mode=0o700)
 atomic(backup/'bootstrap.json',cfg.read_bytes(),0o600)
 for n in manifest['before']:atomic(backup/n,(DEST/n).read_bytes(),0o600)
 for n in ['registry.py','client.py','worker.py']:atomic(DEST/n,(stage/'maas_bootstrap'/n).read_bytes(),0o644)
 conf.update(image_id='tower-e5-registry-v1',allowed_registry_endpoints=['10.11.31.1:18443'])
 atomic(cfg,(json.dumps(conf,indent=2)+'\n').encode(),0o600)
 assert not any((state/n).exists() for n in STATE_FILES) and not Path(conf['worker_data_dir']).exists()
 assert not run(['docker','ps','-aq']) and docker_pid==run(['systemctl','show','docker','-p','MainPID','--value'])
 assert boot==Path('/proc/sys/kernel/random/boot_id').read_text().strip()
 result={'hostname':host,'boot_id':boot,'source_commit':manifest['source_commit'],'hashes':manifest['after'],
         'image_id':conf['image_id'],'allowed_registry_endpoints':conf['allowed_registry_endpoints'],
         'verified_release':metadata,'layout_version':spec['version'],'layout_sha256':spec['sha256'],
         'config_fetched_and_verified_in_memory':True,'registry_credentials_persisted':False,
         'instance_state_absent':True,'native_identity_absent':True,'containers_absent':True,
         'template_guard':'PROTECTED_HOST_WORKER_FORBIDDEN','vm_boot_unchanged':True,'docker_pid_unchanged':True,
         'worker_started':False,'apply_executed':False,'snapshot_created':False}
 atomic(backup/'installed.json',(json.dumps(result,indent=2)+'\n').encode(),0o600)
 print(json.dumps(result))

if __name__=='__main__':
 p=argparse.ArgumentParser();p.add_argument('--stage',required=True);a=p.parse_args();main(Path(a.stage))
