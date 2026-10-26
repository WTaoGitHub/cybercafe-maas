"""Explicit original GPUStack Docker worker launch, with no stop/remove/reboot operations."""
import hashlib,json,os,platform,secrets,socket,stat,subprocess,time,urllib.request
from pathlib import Path
from .client import (BootstrapError,LocalState,HTTPSConfigFetcher,ReleaseVerifier,atomic_json,
                     read_file,NoRedirect)

NAME='maas-gpustack-worker'
LABEL='com.cybercafe.maas.bootstrap'
# Retired 002 names/addresses may be reused by a new test1 clone.
PROTECTED_HOSTS={'xz-31-001'}


def run(args, optional=False):
    try:
        p=subprocess.run(args,capture_output=True,text=True,timeout=20)
        if p.returncode:
            if optional:return None
            raise BootstrapError('RUNTIME_COMMAND_FAILED')
        return p.stdout.strip()
    except (OSError,subprocess.TimeoutExpired):
        if optional:return None
        raise BootstrapError('RUNTIME_COMMAND_UNAVAILABLE') from None


def guard_clone_host():
    host=platform.node().lower()
    # Administrator identifies TOWER as image maintenance; cover newly named templates.
    if host in PROTECTED_HOSTS or host=='tower' or host.startswith('tower-'):
        raise BootstrapError('PROTECTED_HOST_WORKER_FORBIDDEN')
    if os.geteuid()!=0 or platform.system()!='Linux':
        raise BootstrapError('LINUX_ROOT_REQUIRED')
    # Also reject a renamed controller retaining its known address.
    rows=json.loads(run(['ip','-j','-4','address','show']))
    if any(a.get('local') == '10.11.31.1' for n in rows for a in n.get('addr_info',[])):
        raise BootstrapError('PROTECTED_HOST_WORKER_FORBIDDEN')


class NativeDockerWorker:
    def guard(self):guard_clone_host()

    def inspect(self):
        raw=run(['docker','inspect',NAME],optional=True)
        if raw is None:return None
        try:
            row=json.loads(raw)[0];labels=row['Config'].get('Labels') or {}
            if labels.get(LABEL)!='worker-v1':raise BootstrapError('WORKER_NAME_COLLISION')
            return {'running':row['State']['Running'],'fingerprint':labels.get(LABEL+'.fingerprint'),
                    'id':row['Id'],'status':row['State']['Status']}
        except BootstrapError:raise
        except Exception:raise BootstrapError('RUNTIME_STATE_INVALID') from None

    def check_environment(self,settings,envelope):
        if platform.machine()!=settings.data['architecture']:raise BootstrapError('ARCHITECTURE_MISMATCH')
        info=json.loads(run(['docker','info','--format','{{json .}}']))
        if 'nvidia' not in info.get('Runtimes',{}):raise BootstrapError('NVIDIA_RUNTIME_UNAVAILABLE')
        run(['nvidia-container-cli','info'])
        gpu=run(['nvidia-smi','--query-gpu=uuid,driver_version','--format=csv,noheader'])
        lines=gpu.splitlines()
        if len(lines)!=1 or not lines[0].split(',')[0].strip().startswith('GPU-'):
            raise BootstrapError('SINGLE_GPU_REQUIRED')
        image=envelope['manifest']['worker_image_digest']
        data=run(['docker','image','inspect',image],optional=True)
        if data is None:raise BootstrapError('PINNED_WORKER_IMAGE_UNAVAILABLE')
        image_row=json.loads(data)[0]
        if image not in (image_row.get('RepoDigests') or []):raise BootstrapError('WORKER_IMAGE_DIGEST_MISMATCH')
        ids=run(['docker','ps','-aq']).split()
        if ids:
            for c in json.loads(run(['docker','inspect',*ids])):
                if c['Name'].lstrip('/')==NAME:continue
                env=c['Config'].get('Env') or [];cmd=c['Config'].get('Cmd') or []
                if any(x.startswith('GPUSTACK_SERVER_URL=') and x.split('=',1)[1] for x in env) or '--server-url' in cmd:
                    raise BootstrapError('OTHER_WORKER_PRESENT')
        from urllib.parse import urlsplit
        u=urlsplit(envelope['manifest']['server_url'])
        try:
            with socket.create_connection((u.hostname,u.port or (443 if u.scheme=='https' else 80)),timeout=3):pass
        except OSError:raise BootstrapError('NATIVE_SERVER_UNREACHABLE') from None

    def create(self,settings,envelope,metadata,identity,env_file):
        data=Path(settings.data['worker_data_dir'])
        data.mkdir(mode=0o700,parents=True,exist_ok=True)
        if not stat.S_ISDIR(data.lstat().st_mode) or data.stat().st_mode&0o077:
            raise BootstrapError('NATIVE_DIRECTORY_UNTRUSTED')
        # Keep restart=no so daemon/host startup cannot bypass the next bootstrap configuration check.
        image=envelope['manifest']['worker_image_digest']
        run(['docker','create','--name',NAME,'--label',LABEL+'=worker-v1',
             '--label',LABEL+'.fingerprint='+metadata['fingerprint'],
             '--restart=no','--privileged','--gpus','all','--network','host',
             '--ulimit','nofile=65535:65535','--env-file',str(env_file),
             '--volume',str(data)+':/var/lib/gpustack',
             '--volume','/var/run/docker.sock:/var/run/docker.sock',image])

    def start(self):run(['docker','start',NAME])

    def registered(self,settings,envelope,identity):
        data=Path(settings.data['worker_data_dir'])
        if not data.exists() or data.stat().st_mode&0o077:return None
        try:
            # Native GPUStack may create mode644 files; directory700 protects their contents.
            token=read_file(data/'worker_token').decode().strip()
            uuid=read_file(data/'worker_uuid').decode().strip()
            name=read_file(data/'worker_name').decode().strip()
            if name!=identity['worker_name'] or not token or not uuid:return None
            url=envelope['manifest']['server_url']+'/v2/workers?me=true'
            opener=urllib.request.build_opener(urllib.request.ProxyHandler({}),NoRedirect())
            req=urllib.request.Request(url,headers={'Authorization':'Bearer '+token})
            with opener.open(req,timeout=3) as r:
                raw=r.read(1024*1024+1)
                if len(raw)>1024*1024:return None
                rows=json.loads(raw)['items']
            if len(rows)!=1:return None
            w=rows[0]
            if w['cluster_id']!=settings.data['cluster_id'] or w['worker_uuid']!=uuid or w['name']!=name:return None
            return {'worker_id':w['id'],'worker_name':name,'cluster_id':w['cluster_id'],'native_state':w['state']}
        except Exception:return None


def identity_for(state,settings):
    identity=state.read('identity.json')
    if identity:
        if identity.get('cluster_id')!=settings.data['cluster_id']:
            raise BootstrapError('NATIVE_CLUSTER_CHANGE_REQUIRES_REPROVISION')
        if not isinstance(identity.get('worker_name'),str) or not identity['worker_name'].startswith('e5-'):
            raise BootstrapError('LOCAL_IDENTITY_INVALID')
        return identity
    data=Path(settings.data['worker_data_dir'])
    if data.exists() and any(data.iterdir()):raise BootstrapError('NATIVE_IDENTITY_WITHOUT_LOCAL_OWNER')
    identity={'worker_name':'e5-'+secrets.token_hex(12),'cluster_id':settings.data['cluster_id']}
    atomic_json(state.root/'identity.json',identity)
    return identity


def write_env(path,envelope,identity):
    # Docker env-file is data, never sourced by a shell. Token syntax is verified before here.
    values={'GPUSTACK_SERVER_URL':envelope['manifest']['server_url'],
            'GPUSTACK_TOKEN':envelope['registration']['token'],
            'GPUSTACK_DATA_DIR':'/var/lib/gpustack','GPUSTACK_WORKER_NAME':identity['worker_name'],
            'GPUSTACK_WORKER_PORT':'10150','GPUSTACK_METRICS_PORT':'10161'}
    text=''.join(k+'='+v+'\n' for k,v in values.items())
    if any('\n' in v or '\r' in v for v in values.values()):raise BootstrapError('ENV_VALUE_INVALID')
    # Secret file gets the same atomic/permission handling as JSON, then an atomic format conversion.
    import tempfile
    fd,tmp=tempfile.mkstemp(prefix='.worker-env-',dir=path.parent)
    try:
        os.fchmod(fd,0o600)
        with os.fdopen(fd,'w') as f:f.write(text);f.flush();os.fsync(f.fileno())
        os.replace(tmp,path)
    finally:
        if os.path.exists(tmp):os.unlink(tmp)


def apply(settings,trigger='manual',fetcher=None,backend=None,timeout=30,sleeper=time.sleep):
    if trigger not in ('boot','manual'):raise BootstrapError('TRIGGER_INVALID')
    runtime=backend or NativeDockerWorker();runtime.guard()
    state=LocalState(settings.data['state_dir'])
    with state.lock():
        try:
            envelope=(fetcher or HTTPSConfigFetcher()).fetch(settings)
            metadata=ReleaseVerifier().verify(settings,envelope)
            current=runtime.inspect()
            was_running=bool(current and current['running'])
            applied=state.read('applied.json')
            if applied and applied['cluster_id']!=metadata['cluster_id']:
                raise BootstrapError('NATIVE_CLUSTER_CHANGE_REQUIRES_REPROVISION')
            if current and current['fingerprint']!=metadata['fingerprint']:
                # Includes stopped containers: require operator maintenance/removal, never rm implicitly.
                raise BootstrapError('NEEDS_MAINTENANCE')
            runtime.check_environment(settings,envelope)
            target=state.prepare(envelope,metadata)
            identity=identity_for(state,settings)
            if current is None:
                env=target.parent/'worker.env';write_env(env,envelope,identity)
                runtime.create(settings,envelope,metadata,identity,env)
                # Recover a start failure using this owned container, not another docker create.
                runtime.start()
            elif not current['running']:
                runtime.start()
            deadline=time.monotonic()+timeout
            while True:
                registration=runtime.registered(settings,envelope,identity)
                if registration:break
                if time.monotonic()>=deadline:raise BootstrapError('REGISTRATION_UNCONFIRMED')
                sleeper(min(1,max(0,deadline-time.monotonic())))
            code='UNCHANGED' if was_running and applied and applied['fingerprint']==metadata['fingerprint'] else 'APPLIED'
            result={'code':code,'trigger':trigger,**metadata,**registration}
            atomic_json(state.root/'applied.json',result)
            atomic_json(state.root/'last-result.json',result)
            return result
        except BootstrapError as e:
            atomic_json(state.root/'last-result.json',{'code':e.code,'trigger':trigger})
            raise
