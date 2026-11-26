#!/usr/bin/env python3
"""Build a public E5 handoff bundle from explicit source files. Never include credentials."""
import argparse,gzip,hashlib,io,json,subprocess,tarfile
from pathlib import Path
REPO=Path(__file__).resolve().parents[2]
LEGACY_TRUST_FILES = (
    'results/maas-e5-preparation/bootstrap-server.crt',
    'results/maas-e5-preparation/bootstrap-release-signing.pub',
)
SOURCES=[
 'maas_bootstrap/__init__.py','maas_bootstrap/client.py','maas_bootstrap/storage.py','maas_bootstrap/nfs.py','maas_bootstrap/registry.py','maas_bootstrap/handoff.py','maas_bootstrap/worker.py','maas_bootstrap/cli.py',
 'maas_bootstrap/execution.py','maas_bootstrap/worker_limits.py','maas_bootstrap/receipt_replay.py',
 'maas_platform/__init__.py','maas_platform/finish_receipt.py','maas_platform/runtime_proof.py',
 'maas_bootstrap/runtime_witness.py','deploy/bootstrap/clone/maas-pp-runtime-witness.service',
 'maas_bootstrap/delivery.py','maas_bootstrap/enrollment.py','maas_bootstrap/startup.py','maas_bootstrap/clone_boundary.py',
 'maas_bootstrap/shutdown_auth.py','maas_bootstrap/shutdown_events.py',
 'maas_bootstrap/service.py','maas_bootstrap/publisher.py',
 'scripts/maas/prepare_worker_image.py','scripts/maas/inspect_worker_template.py',
 'scripts/maas/verify_worker_bundle.py','requirements-bootstrap-client.txt',
 'deploy/bootstrap/clone/maas-worker-bootstrap','deploy/bootstrap/clone/maas-worker-bootstrap.service',
 'deploy/bootstrap/clone/maas-worker-bootstrap.path','deploy/bootstrap/clone/maas-worker-bootstrap.timer',
 'deploy/bootstrap/clone/maas-worker-shutdown-notify.service',
 'deploy/bootstrap/clone/maas-worker-receipts.service','deploy/bootstrap/clone/maas-worker-receipts.timer',
 'deploy/bootstrap/clone/shutdown-events.example.json','deploy/bootstrap/shutdown-events.env.example',
 'deploy/bootstrap/clone/network/maas-clone-boundary.service','deploy/bootstrap/clone/network/20-clone-boundary.conf',
 'deploy/bootstrap/clone/image-settings.example.json','deploy/bootstrap/clone/README.md',
 'deploy/bootstrap/clone/model-nfs.example.json','deploy/bootstrap/clone/NFS.md',
 'deploy/bootstrap/nfs-server/README.md','deploy/bootstrap/nfs-server/maas-models.conf.example',
 'deploy/bootstrap/nfs-server/maas-models.exports.example','deploy/bootstrap/nfs-server/20-maas-model-nfs-network.conf.example',
 'deploy/bootstrap/clone/HANDOFF.md',*LEGACY_TRUST_FILES]


def build(output, *, source_only=False):
    output=Path(output)
    if output.exists():raise ValueError('Output already exists')
    revision=subprocess.check_output(['git','rev-parse','HEAD'],cwd=REPO,text=True).strip()
    # A referenced commit must actually contain the packaged files unchanged.
    files={}
    for name in SOURCES:
        if source_only and name in LEGACY_TRUST_FILES:
            continue
        data=(REPO/name).read_bytes()
        committed=subprocess.check_output(['git','show',revision+':'+name],cwd=REPO)
        if data!=committed:raise ValueError('Commit source files before building')
        files[name]=(data,0o755 if name.endswith('/maas-worker-bootstrap') else 0o644)
    manifest={'schema_version':1,'source_commit':revision,'contains_secrets':False,'deployed_to_tower':False,
              'artifact_type':'worker-source-bundle' if source_only else 'source-and-trust-bundle',
              'not_included':['shared registration code','native registration token','private keys',
                              'GPUStack container image','Ubuntu disk image or platform snapshot'],
              'files':{n:{'sha256':hashlib.sha256(data).hexdigest(),'mode':mode} for n,(data,mode) in files.items()}}
    if source_only:
        manifest['not_included'].append('site-specific trust material')
    else:
        manifest.update(image_id='tower-e5-pilot-v1', cluster_id=3)
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
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--output',required=True)
    p.add_argument('--source-only',action='store_true',help='Use the target worker’s existing trust configuration')
    a=p.parse_args()
    print(json.dumps(build(a.output,source_only=a.source_only),indent=2))
