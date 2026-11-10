#!/usr/bin/env python3
"""Install E5 files into an OFFLINE Ubuntu filesystem; never change the running host.

Does not generalize OS identity, fetch images, install packages, or create a disk image.
The base-image supplier owns those steps. Reject existing instance state instead of deleting it.
"""
import argparse,hashlib,json,sys
from pathlib import Path
REPO=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(REPO))
from maas_bootstrap.client import BootstrapError,LocalSettings,SharedCodeFileProvider,StaticEndpointResolver,read_file


def safe_target(root,relative):
    target=root/relative
    cursor=target
    while cursor!=root:
        if cursor.is_symlink():raise BootstrapError('IMAGE_SYMLINK_PATH')
        cursor=cursor.parent
    return target


def build(root,settings_path):
    original=Path(root).absolute()
    if original.is_symlink():raise BootstrapError('OFFLINE_ROOT_REQUIRED')
    root=original.resolve()
    # Refuse /, standard live roots, and accidental preparation of the current host.
    if root==Path('/') or not (root/'etc/os-release').is_file() or (root/'proc/self').exists():
        raise BootstrapError('OFFLINE_UBUNTU_ROOT_REQUIRED')
    release_path=root/'etc/os-release'
    if release_path.is_symlink():
        target=Path(release_path.readlink())
        if target.is_absolute():target=root/str(target).lstrip('/')
        else:target=release_path.parent/target
        resolved=target.resolve()
        if not resolved.is_relative_to(root):raise BootstrapError('IMAGE_SYMLINK_PATH')
        release_path=safe_target(root,str(resolved.relative_to(root)))
    else:release_path=safe_target(root,'etc/os-release')
    release=release_path.read_text()
    if not any(line.strip() in ('ID=ubuntu','ID="ubuntu"') for line in release.splitlines()):
        raise BootstrapError('UBUNTU_BASE_REQUIRED')
    settings=LocalSettings(settings_path);StaticEndpointResolver().resolve(settings)
    code=SharedCodeFileProvider().get(settings)
    cert=read_file(settings.data['tls_certificate_file']);pub=read_file(settings.data['release_public_key_file'])
    import ssl
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
    try:
        ssl.create_default_context(cadata=cert.decode())
        if not isinstance(serialization.load_pem_public_key(pub),Ed25519PublicKey):raise ValueError()
    except Exception:raise BootstrapError('IMAGE_TRUST_MATERIAL_INVALID') from None
    if settings.data['architecture']!='x86_64':raise BootstrapError('IMAGE_ARCHITECTURE_UNSUPPORTED')
    diskless='worker_start_grant_file' in settings.data
    state='/run/maas-bootstrap' if diskless else '/var/lib/maas-bootstrap'
    if settings.data['state_dir']!=state or settings.data['worker_data_dir']!=state+'/gpustack-worker':
        raise BootstrapError('IMAGE_STATE_PATH_UNSUPPORTED')
    if diskless and settings.data['worker_start_grant_file']!='/run/maas-start/boot-grant':
        raise BootstrapError('IMAGE_STATE_PATH_UNSUPPORTED')
    # Never erase the supplier's SSH access or registered identities. Fail for manual generalization.
    blockers=[]
    for name in ('var/lib/gpustack','var/lib/maas-bootstrap','run/maas-bootstrap','run/maas-start',
                 'etc/gpustack','root/.gpustack','var/lib/docker/containers'):
        p=safe_target(root,name)
        if p.exists() and (not p.is_dir() or any(p.iterdir())):blockers.append(name)
    for name in ('etc/machine-id','var/lib/dbus/machine-id'):
        p=safe_target(root,name)
        if p.exists() and p.read_text().strip() not in ('','uninitialized'):blockers.append(name)
    ssh=safe_target(root,'etc/ssh')
    if ssh.exists():
        blockers.extend(str(p.relative_to(root)) for p in ssh.glob('ssh_host_*_key*'))
    for name in ('etc/systemd/system/cybercafe-agent.service','etc/systemd/system/gpustack.service',
                 'etc/systemd/system/gpustack-worker.service'):
        p=safe_target(root,name)
        if p.exists():blockers.append(name)
    if blockers:raise BootstrapError('IMAGE_GENERALIZATION_REQUIRED:'+','.join(sorted(blockers)))
    cfg={k:settings.data[k] for k in (
        'schema_version','config_service_url','release_key_id','state_dir','worker_data_dir','model_root','model_cache_dir',
        'site_id','batch_id','cluster_id','image_id','architecture','allowed_server_urls','cloud_backup')}
    if 'allowed_registry_endpoints' in settings.data:cfg['allowed_registry_endpoints']=settings.data['allowed_registry_endpoints']
    if diskless:cfg['worker_start_grant_file']=settings.data['worker_start_grant_file']
    if settings.data.get('worker_start_automatic'):cfg['worker_start_automatic']=True
    cfg.update(shared_code_file='/etc/maas/shared-code',tls_certificate_file='/etc/maas/server.crt',
               release_public_key_file='/etc/maas/release-signing.pub')
    files={
        'etc/maas/bootstrap.json':(json.dumps(cfg,indent=2).encode()+b'\n',0o600),
        'etc/maas/shared-code':(code.encode()+b'\n',0o600),
        'etc/maas/server.crt':(cert,0o644),
        'etc/maas/release-signing.pub':(pub,0o644),
    }
    for name in ('__init__.py','client.py','storage.py','registry.py','handoff.py','worker.py','cli.py'):
        files['opt/maas/lib/maas_bootstrap/'+name]=((REPO/'maas_bootstrap'/name).read_bytes(),0o644)
    for name,dest,mode in (
        ('maas-worker-bootstrap','opt/maas/bin/maas-worker-bootstrap',0o755),
        ('maas-worker-bootstrap.service','etc/systemd/system/maas-worker-bootstrap.service',0o644)):
        files[dest]=((REPO/'deploy/bootstrap/clone'/name).read_bytes(),mode)
    boot_unit='maas-worker-bootstrap.service'
    if diskless:
        path_unit='maas-worker-bootstrap.path'
        if not cfg.get('worker_start_automatic'):boot_unit=path_unit
        files['etc/systemd/system/'+path_unit]=((REPO/'deploy/bootstrap/clone'/path_unit).read_bytes(),0o644)
        files['opt/maas/lib/maas_bootstrap/delivery.py']=((REPO/'maas_bootstrap/delivery.py').read_bytes(),0o644)
    link='etc/systemd/system/multi-user.target.wants/'+boot_unit
    manifest='opt/maas/image-manifest.json'
    # Full preflight before writing; never overwrite existing image files or links.
    enable_dir='etc/systemd/system/multi-user.target.wants/'
    for name in [*files,enable_dir+'maas-worker-bootstrap.service',enable_dir+'maas-worker-bootstrap.path',manifest]:
        p=safe_target(root,name)
        if p.exists() or p.is_symlink():raise BootstrapError('IMAGE_DESTINATION_EXISTS')
    for name,(contents,mode) in files.items():
        p=safe_target(root,name);p.parent.mkdir(parents=True,exist_ok=True)
        with p.open('xb') as f:f.write(contents)
        p.chmod(mode)
    safe_target(root,'etc/maas').chmod(0o700)
    p=safe_target(root,link);p.parent.mkdir(parents=True,exist_ok=True)
    p.symlink_to('/etc/systemd/system/'+boot_unit)
    result={'schema_version':1,'image_id':cfg['image_id'],'cluster_id':cfg['cluster_id'],
            'architecture':cfg['architecture'],'state_included':False,'worker_started':False,
            'hashes':{n:hashlib.sha256(b).hexdigest() for n,(b,m) in files.items() if n!='etc/maas/shared-code'},
            'required_before_sealing':['root ownership of installed files','Python3 with cryptography==41.0.7',
                'Docker and NVIDIA Container Toolkit configured','pinned worker image cached',
                'native instance identity absent; supplier OS identity preserved for pilot',
                'fresh clone hostname does not match protected controller or tower prefix',
                'dedicated public model root and cache prepared by bootstrap; native identity volume excluded']}
    p=safe_target(root,manifest);p.write_text(json.dumps(result,indent=2)+'\n');p.chmod(0o644)
    return result


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--root',required=True)
    p.add_argument('--settings',required=True);a=p.parse_args()
    try:print(json.dumps(build(a.root,a.settings)));return 0
    except BootstrapError as e:print(json.dumps({'code':e.code}));return 1
    except Exception:print(json.dumps({'code':'IMAGE_PREPARATION_FAILED'}));return 1
if __name__=='__main__':raise SystemExit(main())
