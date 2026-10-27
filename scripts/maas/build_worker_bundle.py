#!/usr/bin/env python3
"""Build a public E5 handoff bundle from explicit source files. Never include credentials."""
import argparse,gzip,hashlib,io,json,subprocess,tarfile
from pathlib import Path
REPO=Path(__file__).resolve().parents[2]
SOURCES=[
 'maas_bootstrap/__init__.py','maas_bootstrap/client.py','maas_bootstrap/storage.py','maas_bootstrap/registry.py','maas_bootstrap/worker.py','maas_bootstrap/cli.py',
 'scripts/maas/prepare_worker_image.py','scripts/maas/inspect_worker_template.py',
 'scripts/maas/verify_worker_bundle.py','requirements-bootstrap-client.txt',
 'deploy/bootstrap/clone/maas-worker-bootstrap','deploy/bootstrap/clone/maas-worker-bootstrap.service',
 'deploy/bootstrap/clone/image-settings.example.json','deploy/bootstrap/clone/README.md',
 'deploy/bootstrap/clone/HANDOFF.md',
 'results/maas-e5-preparation/bootstrap-server.crt','results/maas-e5-preparation/bootstrap-release-signing.pub']


def build(output):
    output=Path(output)
    if output.exists():raise ValueError('Output already exists')
    revision=subprocess.check_output(['git','rev-parse','HEAD'],cwd=REPO,text=True).strip()
    # A referenced commit must actually contain the packaged files unchanged.
    files={}
    for name in SOURCES:
        data=(REPO/name).read_bytes()
        committed=subprocess.check_output(['git','show',revision+':'+name],cwd=REPO)
        if data!=committed:raise ValueError('Commit source files before building')
        files[name]=(data,0o755 if name.endswith('/maas-worker-bootstrap') else 0o644)
    manifest={'schema_version':1,'source_commit':revision,'contains_secrets':False,'deployed_to_tower':False,
              'image_id':'tower-e5-pilot-v1','cluster_id':3,'artifact_type':'source-and-trust-bundle',
              'not_included':['shared registration code','native registration token','private keys',
                              'GPUStack container image','Ubuntu disk image or platform snapshot'],
              'files':{n:{'sha256':hashlib.sha256(data).hexdigest(),'mode':mode} for n,(data,mode) in files.items()}}
    files['bundle-manifest.json']=((json.dumps(manifest,indent=2)+'\n').encode(),0o644)
    output.parent.mkdir(parents=True,exist_ok=True)
    with output.open('xb') as raw:
        with gzip.GzipFile(fileobj=raw,mode='wb',filename='',mtime=0) as compressed:
            with tarfile.open(fileobj=compressed,mode='w') as archive:
                for name,(data,mode) in sorted(files.items()):
                    info=tarfile.TarInfo(name);info.size=len(data);info.mode=mode;info.mtime=0
                    info.uid=info.gid=0;info.uname=info.gname='root'
                    archive.addfile(info,io.BytesIO(data))
    return {'source_commit':revision,'sha256':hashlib.sha256(output.read_bytes()).hexdigest(),
            'bytes':output.stat().st_size,'files':len(files),'deployed_to_tower':False}

if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--output',required=True);a=p.parse_args()
    print(json.dumps(build(a.output),indent=2))
