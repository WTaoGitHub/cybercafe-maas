"""Explicit worker6 maintenance migration, preserving native identity and old container."""
import hashlib,json,os,subprocess,sys
from pathlib import Path
ROOT=Path('/root/wtaobj/registry-access-v1-install')
DEST=Path('/opt/maas/lib/maas_bootstrap')
BACKUP='maas-gpustack-worker-before-registry-v1-20261027'
def run(args):return subprocess.check_output(args,text=True,stderr=subprocess.PIPE,timeout=90).strip()
def save(path,v):
 with path.open('w') as f:os.fchmod(f.fileno(),0o600);json.dump(v,f,indent=2);f.flush();os.fsync(f.fileno())

def main():
 assert os.getuid()==0 and Path('/proc/sys/kernel/random/boot_id').read_text().strip()=='ac1c964a-aec1-48af-a53a-ae85bc6f2a36'
 assert not ROOT.exists();assert run(['docker','ps','--format','{{.Names}}']).splitlines()==['maas-gpustack-worker']
 ROOT.mkdir(mode=0o700)
 cfg=Path('/etc/maas/bootstrap.json');old=json.loads(run(['docker','inspect','maas-gpustack-worker']))[0]
 assert not (DEST/'registry.py').exists()
 data=Path('/var/lib/maas-bootstrap/gpustack-worker');names=['worker_token','worker_name','worker_uuid','jwt_secret_key']
 before={n:hashlib.sha256((data/n).read_bytes()).hexdigest() for n in names}
 save(ROOT/'before.private.json',{'container':old,'identity_hashes':before,'config':json.loads(cfg.read_text()),'docker_pid':run(['systemctl','show','docker','-p','MainPID','--value'])})
 assert hashlib.sha256((DEST/'client.py').read_bytes()).hexdigest()=='16a50b83012b74bae4a4296dc2ad93c99d48dcbbd3f870a3d91f2eeb84a954c9'
 assert hashlib.sha256((DEST/'worker.py').read_bytes()).hexdigest()=='5010d4c7d0fa21d97f868ee03ec5e2cca556db9324ba5aed9d336447fd449cf2'
 for n in ['client.py','worker.py']:(ROOT/n).write_bytes((DEST/n).read_bytes());(ROOT/n).chmod(0o600)
 for n in ['client.py','worker.py','registry.py']:
  raw=(Path('/root/registry-client')/n).read_bytes()
  assert hashlib.sha256(raw).hexdigest()==json.loads(Path('/root/registry-client/hashes.json').read_text())[n]
  compile(raw,str(DEST/n),'exec')
  # Atomic replacement; staged files are nonsecret and known from the committed source.
  tmp=DEST/(n+'.new');tmp.write_bytes(raw);tmp.chmod(0o644);os.replace(tmp,DEST/n)
 sys.path.insert(0,'/opt/maas/lib')
 from maas_bootstrap.client import atomic_json,LocalSettings
 conf=json.loads(cfg.read_text());conf['image_id']='tower-e5-registry-v1';conf['allowed_registry_endpoints']=['10.11.31.1:18443'];atomic_json(cfg,conf)
 # Controller publishes the signed release before this explicit operator replacement.
 run(['docker','stop','--timeout','30','maas-gpustack-worker']);run(['docker','rename','maas-gpustack-worker',BACKUP])
 receipt=Path(conf['state_dir'])/'maintenance-backups.json';assert not receipt.exists()
 atomic_json(receipt,{'schema_version':1,'container_ids':[old['Id']]})
 result=json.loads(run(['/opt/maas/bin/maas-worker-bootstrap','apply','--trigger','manual']))
 assert result['worker_id']==6 and result['layout_version']=='registry-access-v1'
 assert all(hashlib.sha256((data/n).read_bytes()).hexdigest()==v for n,v in before.items())
 new=json.loads(run(['docker','inspect','maas-gpustack-worker']))[0]
 repeat=json.loads(run(['/opt/maas/bin/maas-worker-bootstrap','apply','--trigger','manual']));assert repeat['code']=='UNCHANGED'
 assert run(['systemctl','show','docker','-p','MainPID','--value'])==json.loads((ROOT/'before.private.json').read_text())['docker_pid']
 public={'worker_id':6,'old_container_id':old['Id'],'new_container_id':new['Id'],'identity_files_unchanged':True,'repeat_apply':repeat['code'],
         'layout_version':result['layout_version'],'layout_sha256':result['layout_sha256'],'vm_boot_unchanged':True,'docker_pid_unchanged':True}
 save(ROOT/'installed.json',public);print(json.dumps(public))

if __name__=='__main__':main()
