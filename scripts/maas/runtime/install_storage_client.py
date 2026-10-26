"""Explicit storage-v1 client upgrade; prepares public directories, never apply.

The operator stages three public files after a private Mac backup. No worker,
model, Docker or VM restart. Reject source drift rather than overwriting it.
"""
import argparse,hashlib,json,os,socket,tempfile
from pathlib import Path
EXPECTED={'client.py':'16a50b83012b74bae4a4296dc2ad93c99d48dcbbd3f870a3d91f2eeb84a954c9',
          'worker.py':'5010d4c7d0fa21d97f868ee03ec5e2cca556db9324ba5aed9d336447fd449cf2',
          'storage.py':'741d9eb89dd7429d36e6166580fe2e443f62976de21d517192fe4545352114d4'}
BEFORE={'client.py':'709ffe5356d8677ae5a6d41aacbaa76b60ac11a222fc60f16bebbc6c4b808767',
        'worker.py':'bf34d3d93ae362f3de985a31a81eea5495dcd5739fa6243be251391bfcc6f994'}


def atomic(path,content,mode):
    fd,tmp=tempfile.mkstemp(prefix='.storage-install-',dir=path.parent)
    with os.fdopen(fd,'wb') as f:f.write(content);f.flush();os.fchmod(f.fileno(),mode);os.fchown(f.fileno(),0,0);os.fsync(f.fileno())
    os.replace(tmp,path)
    fd=os.open(path.parent,os.O_DIRECTORY);os.fsync(fd);os.close(fd)


def main(role):
    assert os.getuid()==0
    host=socket.gethostname()
    assert (host=='XZ-31-002') if role=='clone' else (host.lower()=='tower' or host.lower().startswith('tower-'))
    dest=Path('/opt/maas/lib/maas_bootstrap');backup=Path('/root/wtaobj/e5-storage-v1-install')
    assert not backup.exists() and not (dest/'storage.py').exists()
    data={}
    for name,sha in EXPECTED.items():
        raw=(Path('/root')/(name+'.storage-v1')).read_bytes()
        assert hashlib.sha256(raw).hexdigest()==sha;compile(raw,str(dest/name),'exec');data[name]=raw
    for name,sha in BEFORE.items():assert hashlib.sha256((dest/name).read_bytes()).hexdigest()==sha
    backup.mkdir(mode=0o700)
    for name in BEFORE:atomic(backup/name,(dest/name).read_bytes(),0o600)
    cfg=Path('/etc/maas/bootstrap.json');atomic(backup/'bootstrap.json',cfg.read_bytes(),0o600)
    for name in ['storage.py','client.py','worker.py']:atomic(dest/name,data[name],0o644)
    d=json.loads(cfg.read_text());d.update(model_root='/srv/maas/models',model_cache_dir='/srv/maas/model-cache')
    atomic(cfg,(json.dumps(d,indent=2)+'\n').encode(),0o600)
    import sys
    sys.path.insert(0,'/opt/maas/lib')
    from maas_bootstrap.client import LocalSettings,BootstrapError
    from maas_bootstrap.storage import prepare
    from maas_bootstrap.worker import guard_clone_host
    storage=prepare(LocalSettings(cfg))
    if role=='template':
        try:guard_clone_host()
        except BootstrapError as e:assert e.code=='PROTECTED_HOST_WORKER_FORBIDDEN'
        else:raise AssertionError('Template guard failed')
    value={'hostname':host,'role':role,'source_commit':'e202112','hashes':EXPECTED,'storage':storage,
           'worker_started':False,'vm_rebooted':False,'snapshot_created':False}
    atomic(backup/'installed.json',(json.dumps(value,indent=2)+'\n').encode(),0o600)
    print(json.dumps(value))


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--role',choices=['clone','template'],required=True);a=p.parse_args();main(a.role)
