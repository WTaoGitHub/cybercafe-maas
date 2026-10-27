"""One-shot actual mount/access proof on the independent clone; no restarts.

Uses a unique disposable file for cache and RO checks, never real model/identity
contents. Does not attempt privileged host escape; this is not sandbox proof.
"""
import errno,json,os,secrets,subprocess
from pathlib import Path


def run(args,stdin=None):return subprocess.check_output(args,input=stdin,text=True,stderr=subprocess.PIPE,timeout=30).strip()


def main(model_name="maas-ocr-e5-r2",out=Path("/root/wtaobj/e5-storage-v1-install/mount-proof.json")):
    if out.exists():raise RuntimeError('Mount proof exists; inspect before another run')
    rows=json.loads(run(['docker','inspect',*run(['docker','ps','-q']).split()]))
    worker=next(c for c in rows if c['Name']=='/maas-gpustack-worker')
    model=next(c for c in rows if c['Name'].startswith('/'+model_name+'-') and c['Name'].endswith('-run-0'))
    def mounts(c):return {m['Destination']:{'source':m['Source'],'rw':m['RW']} for m in c['Mounts']}
    wm,mm=mounts(worker),mounts(model)
    assert set(mm)=={'/srv/maas/models','/srv/maas/model-cache'}
    assert mm['/srv/maas/models']=={'source':'/srv/maas/models','rw':False}
    assert mm['/srv/maas/model-cache']=={'source':'/srv/maas/model-cache','rw':True}
    assert wm['/var/lib/gpustack']['source']=='/var/lib/maas-bootstrap/gpustack-worker'
    env=dict(e.split('=',1) for e in worker['Config']['Env'] if '=' in e)
    assert '/var/lib/gpustack' in env['GPUSTACK_RUNTIME_DEPLOY_MIRRORED_DEPLOYMENT_IGNORE_VOLUMES'].split(';')
    nonce=secrets.token_hex(12);name='.storage-v1-probe-'+nonce
    code="""
import json,os,errno
from pathlib import Path
models=Path('/srv/maas/models');cache=Path('/srv/maas/model-cache')
assert (models/'image-contained/model/deployment.json').is_file()
for n in ['worker_token','worker_uuid','worker_name','jwt_secret_key']:
 assert not (Path('/var/lib/gpustack')/n).exists()
assert not Path('/var/run/docker.sock').exists()
assert not Path('/run/maas-registry-auth').exists()
assert not os.environ.get('GPUSTACK_TOKEN')
if 'DOCKER_CONFIG' in os.environ:assert os.environ['DOCKER_CONFIG']=='/run/maas-registry-auth'
assert not os.environ.get('GPUSTACK_RUNTIME_DEPLOY_DEFAULT_CONTAINER_REGISTRY_PASSWORD')
name=NAME
try:fd=os.open(models/name,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
except OSError as e:assert e.errno==errno.EROFS
else:os.close(fd);raise AssertionError('Published model root was writable')
target=cache/name;target.write_text(NONCE)
print(json.dumps({'model_marker_readable':True,'model_write_denied_EROFS':True,
 'identity_files_absent':True,'docker_socket_absent':True,'registry_auth_absent':True,'native_token_env_absent':True,'docker_config_pointer_inherited':bool(os.environ.get('DOCKER_CONFIG')),'cache_write_succeeded':True}))
""".replace('NAME',repr(name)).replace('NONCE',repr(nonce))
    proof=json.loads(run(['docker','exec','-i',model['Id'],'python3','-'],code))
    path=Path('/srv/maas/model-cache')/name
    assert path.read_text()==nonce;path.unlink()
    proof['cache_write_visible_on_host']=True
    token_present=run(['docker','exec',worker['Id'],'python3','-c',"from pathlib import Path;print(Path('/var/lib/gpustack/worker_token').is_file())"])
    assert token_present=='True';proof['worker_identity_still_present']=True
    proof.update(model_container_id=model['Id'],worker_container_id=worker['Id'],model_mounts=mm,
                 worker_mounts=wm,scope='filesystem sharing only; privileged containers are not a strong security boundary')
    out.parent.mkdir(mode=0o700,parents=True,exist_ok=True)
    out.write_text(json.dumps(proof,indent=2)+'\n');out.chmod(0o600)
    print(json.dumps(proof))


if __name__=='__main__':
    import argparse
    p=argparse.ArgumentParser();p.add_argument('--model-name',default='maas-ocr-e5-r2')
    p.add_argument('--out',default='/root/wtaobj/e5-storage-v1-install/mount-proof.json')
    args=p.parse_args();main(args.model_name,Path(args.out))
