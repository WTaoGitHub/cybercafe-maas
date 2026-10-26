"""Public model storage, separate from native identity and bootstrap secrets."""
import json,os,stat
from pathlib import Path
from .client import BootstrapError,atomic_json,read_file

DEFAULT_MODELS='/srv/maas/models'
DEFAULT_CACHE='/srv/maas/model-cache'
MARKER={'schema_version':1,'weights_policy':'image-contained',
        'purpose':'Public GPUStack local_path marker; weights are in the inference image'}


def paths(data):
    result=[]
    for value in [data.get('model_root',DEFAULT_MODELS),data.get('model_cache_dir',DEFAULT_CACHE)]:
        if not isinstance(value,str) or any(c in value for c in ':\n\r\x00'):
            raise ValueError('Invalid storage path')
        p=Path(value)
        if not p.is_absolute() or str(p)!=value or '..' in p.parts or p.resolve()!=p:
            raise ValueError('Storage path must be canonical without symlinks')
        if value in ['/', '/srv','/root','/home','/etc','/var','/var/lib','/usr','/opt','/tmp','/var/tmp']:
            raise ValueError('Dedicated storage directory required')
        result.append(p)
    private=[Path(data[k]) for k in ['state_dir','worker_data_dir']]+[Path('/var/lib/gpustack')]
    for a,b in [(result[0],result[1])]+[(p,q) for p in result for q in private]:
        if a==b or a in b.parents or b in a.parents:raise ValueError('Storage overlaps private state or another storage root')
    for key in ['shared_code_file','tls_certificate_file','release_public_key_file']:
        p=Path(data[key])
        if any(root==p or root in p.parents for root in result):raise ValueError('Storage contains bootstrap trust/credentials')
    return tuple(result)


def directory(path,create=False):
    if path.resolve()!=path:raise BootstrapError('MODEL_STORAGE_UNTRUSTED')
    if create:path.mkdir(parents=True,mode=0o755,exist_ok=True)
    try:s=path.lstat()
    except OSError:raise BootstrapError('MODEL_STORAGE_NOT_READY') from None
    if not stat.S_ISDIR(s.st_mode) or s.st_uid not in (0,os.geteuid()) or s.st_mode&0o022:
        raise BootstrapError('MODEL_STORAGE_UNTRUSTED')


def prepare(settings):
    try:models,cache=paths(settings.data)
    except ValueError:raise BootstrapError('MODEL_STORAGE_UNTRUSTED') from None
    for p in [models,cache,models/'image-contained',models/'image-contained/model']:directory(p,create=True)
    marker=models/'image-contained/model/deployment.json'
    if marker.is_symlink():raise BootstrapError('MODEL_STORAGE_UNTRUSTED')
    if marker.exists():
        if json.loads(read_file(marker))!=MARKER:raise BootstrapError('MODEL_MARKER_CONFLICT')
    else:
        atomic_json(marker,MARKER);marker.chmod(0o644)
    return status(settings)


def status(settings):
    try:models,cache=paths(settings.data)
    except ValueError:raise BootstrapError('MODEL_STORAGE_UNTRUSTED') from None
    for p in [models,cache,models/'image-contained',models/'image-contained/model']:directory(p)
    if json.loads(read_file(models/'image-contained/model/deployment.json'))!=MARKER:
        raise BootstrapError('MODEL_MARKER_CONFLICT')
    return {'model_root':str(models),'model_cache_dir':str(cache),'marker_path':str(models/'image-contained/model'),
            'published_models_readonly':True,'cache_writable':True}
