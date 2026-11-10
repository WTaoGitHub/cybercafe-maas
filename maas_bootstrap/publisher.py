"""Operator-only immutable release publishing; no HTTP write endpoint or worker action."""
import argparse,base64,contextlib,fcntl,hashlib,json,os,tempfile
from pathlib import Path
from .client import BootstrapError,LocalSettings,ReleaseVerifier,canonical,configured_cluster_id,read_file,SAFE_REVISION


class Publisher:
    def __init__(self,root,settings,key):
        self.root=Path(root);self.settings=settings
        s=self.root.lstat()
        if self.root.is_symlink() or not self.root.is_dir() or s.st_mode&0o077:
            raise BootstrapError('PUBLISH_DIRECTORY_UNTRUSTED')
        self.owner=(s.st_uid,s.st_gid)
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
        try:
            self.key=serialization.load_pem_private_key(read_file(key,secret=True),password=None)
            if not isinstance(self.key,Ed25519PrivateKey):raise ValueError()
        except BootstrapError:raise
        except Exception:raise BootstrapError('SIGNING_KEY_INVALID') from None

    @contextlib.contextmanager
    def lock(self):
        fd=os.open(self.root/'publish.lock',os.O_CREAT|os.O_RDWR|os.O_NOFOLLOW,0o600)
        try:
            try:fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
            except BlockingIOError:raise BootstrapError('PUBLISH_BUSY') from None
            yield
        finally:os.close(fd)

    def atomic(self,path,raw,immutable=False):
        fd,tmp=tempfile.mkstemp(prefix='.publish-',dir=path.parent)
        try:
            os.fchmod(fd,0o600)
            if os.geteuid()==0:os.fchown(fd,*self.owner)
            with os.fdopen(fd,'wb') as f:f.write(raw);f.flush();os.fsync(f.fileno())
            if immutable:os.link(tmp,path)
            else:os.replace(tmp,path)
            directory=os.open(path.parent,os.O_RDONLY)
            try:os.fsync(directory)
            finally:os.close(directory)
        finally:
            if os.path.exists(tmp):os.unlink(tmp)

    def publish(self,manifest,registration,rollback_of=None):
        manifest = {'cluster_id': self.settings.data['cluster_id'], **manifest}
        registration = {'cluster_id': manifest['cluster_id'], **registration}
        with self.lock():return self._publish(manifest,registration,rollback_of)

    def _publish(self,manifest,registration,rollback_of=None):
        envelope={'manifest':manifest,'registration':registration,
                  'manifest_signature':{'algorithm':'Ed25519','key_id':self.settings.data['release_key_id'],
                                        'value':base64.b64encode(self.key.sign(canonical(manifest))).decode()}}
        metadata=ReleaseVerifier().verify(self.settings,envelope)
        policy=json.loads((self.root/'policy.json').read_text())
        if not any((g['site_id'],g['batch_id'],configured_cluster_id(g))==
                   (manifest['site_id'],manifest['batch_id'],manifest['cluster_id']) for g in policy['grants']):
            raise BootstrapError('PUBLISH_SCOPE_NOT_CONFIGURED')
        releases=self.root/'releases'
        if releases.is_symlink():raise BootstrapError('PUBLISH_DIRECTORY_UNTRUSTED')
        releases.mkdir(mode=0o700,exist_ok=True)
        if releases.stat().st_mode&0o077:raise BootstrapError('PUBLISH_DIRECTORY_UNTRUSTED')
        if os.geteuid()==0:os.chown(releases,*self.owner)
        raw=canonical(envelope)+b'\n';target=releases/(metadata['revision']+'.json')
        if target.is_symlink():raise BootstrapError('REVISION_IMMUTABLE')
        if target.exists():
            if target.read_bytes()!=raw:raise BootstrapError('REVISION_IMMUTABLE')
        else:self.atomic(target,raw,immutable=True)
        self.atomic(self.root/'active.json',canonical({'revision':metadata['revision'],
                    'sha256':hashlib.sha256(raw).hexdigest()})+b'\n')
        return {'code':'PUBLISHED',**metadata,'rollback_of':rollback_of,'worker_actions':False}

    def rollback(self,source,new_revision):
        if not SAFE_REVISION.fullmatch(source) or not SAFE_REVISION.fullmatch(new_revision) or source==new_revision:
            raise BootstrapError('ROLLBACK_REVISION_INVALID')
        with self.lock():
            path=self.root/'releases'/(source+'.json')
            if path.is_symlink():raise BootstrapError('PUBLISH_DIRECTORY_UNTRUSTED')
            old=json.loads(path.read_bytes())
            metadata=ReleaseVerifier().verify(self.settings,old)
            if metadata['revision']!=source:raise BootstrapError('ROLLBACK_SOURCE_INVALID')
            manifest={**old['manifest'],'config_revision':new_revision}
            return self._publish(manifest,old['registration'],source)


def main(argv=None):
    p=argparse.ArgumentParser();p.add_argument('--root',required=True);p.add_argument('--settings',required=True)
    p.add_argument('--signing-key',required=True);sub=p.add_subparsers(dest='action',required=True)
    a=sub.add_parser('publish');a.add_argument('--manifest',required=True);a.add_argument('--registration-file',required=True)
    a=sub.add_parser('rollback');a.add_argument('--source',required=True);a.add_argument('--new-revision',required=True)
    a=p.parse_args(argv)
    try:
        publisher=Publisher(a.root,LocalSettings(a.settings),a.signing_key)
        if a.action=='publish':result=publisher.publish(json.loads(read_file(a.manifest)),json.loads(read_file(a.registration_file,secret=True)))
        else:result=publisher.rollback(a.source,a.new_revision)
        print(json.dumps(result));return 0
    except BootstrapError as e:print(json.dumps({'code':e.code}));return 1
    except Exception:print(json.dumps({'code':'PUBLISH_FAILED'}));return 1
if __name__=='__main__':raise SystemExit(main())
