"""Private operator session files; no customer endpoints or credential values."""
import fcntl,json,os,time,uuid
from pathlib import Path


def private_json(path):
    path=Path(path)
    if not path.is_file() or path.is_symlink() or path.stat().st_mode & 0o077:
        raise ValueError('Operator configuration must be a private regular file')
    return json.loads(path.read_text())


def create_private(path,value):
    path=Path(path)
    fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
    with os.fdopen(fd,'w') as f:json.dump(value,f,indent=2);f.write('\n');f.flush();os.fsync(f.fileno())


def new_session(root):
    """Serialize session allocation; incomplete windows require recovery first."""
    root=Path(root);binding=private_json(root/'binding.json')
    fd=os.open(root/'allocation.lock',os.O_CREAT|os.O_RDWR,0o600)
    with os.fdopen(fd,'w') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        active=root/'active.json'
        if active.exists():
            old=private_json(private_json(active)['config'])
            journal=Path(old['journal'])
            if not journal.exists() or private_json(journal).get('phase')!='RESUMED':
                raise RuntimeError('Previous session is incomplete; inspect/recover it before allocating another')
        ident=time.strftime('%Y%m%dT%H%M%SZ',time.gmtime())+'-'+uuid.uuid4().hex[:10]
        folder=root/'sessions'/ident;folder.mkdir(mode=0o700,parents=True)
        cfg={**binding,'journal':str(folder/'journal.json'),'recovery_unit':'maas-live-recover-'+ident.lower()}
        path=folder/'operator.json';create_private(path,cfg)
        temp=root/'active.new';create_private(temp,{'config':str(path),'session':ident});os.replace(temp,active)
        return path


def wait_collector(path,seconds=180,*,clock=time.monotonic,sleep=time.sleep,now=time.time):
    end=clock()+seconds
    while clock()<end:
        try:
            value=json.loads(Path(path).read_text());ts=now()
            if value['ok'] and 0<=ts-value['updated_at']<15 and 0<=ts-value['observed_at']<25:return
        except (OSError,ValueError,KeyError,TypeError):pass
        sleep(1)
    raise RuntimeError('Collector is not fresh and healthy; admission remains paused')
