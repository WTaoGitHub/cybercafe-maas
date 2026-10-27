"""Signed, endpoint-scoped registry access. No pulls, shell login or daemon restart."""
import base64,hashlib,json,re,ssl,stat
from pathlib import Path
from .client import BootstrapError,atomic_json,canonical,read_file

TARGET='/run/maas-registry-auth'
ENDPOINT=re.compile(r'[a-zA-Z0-9](?:[a-zA-Z0-9.-]*[a-zA-Z0-9])?:[1-9][0-9]{0,4}\Z')


def validate(settings,envelope):
 public=envelope['manifest'].get('registry_access')
 payload=envelope['registration'].get('registry_access')
 if public is None:
  if payload is not None:raise BootstrapError('REGISTRY_ACCESS_INVALID')
  return None
 try:
  if set(public)!={'schema_version','endpoint','credential_id','payload_sha256'} or public['schema_version']!=1:raise ValueError()
  endpoint=public['endpoint']
  if not ENDPOINT.fullmatch(endpoint) or int(endpoint.rsplit(':',1)[1])>65535:raise ValueError()
  if endpoint not in settings.data.get('allowed_registry_endpoints',[]):raise BootstrapError('REGISTRY_ENDPOINT_NOT_ALLOWED')
  if not re.fullmatch(r'[A-Za-z0-9_.-]{1,80}',public['credential_id']):raise ValueError()
  if set(payload)!={'certificate_pem','username','password'}:raise ValueError()
  if not re.fullmatch(r'[A-Za-z0-9_.-]{1,80}',payload['username']):raise ValueError()
  password=payload['password']
  if not isinstance(password,str) or not 24<=len(password)<=512 or any(ord(c)<33 or ord(c)>126 for c in password):raise ValueError()
  if hashlib.sha256(canonical(payload)).hexdigest()!=public['payload_sha256']:raise ValueError()
  cert=payload['certificate_pem'];ssl.create_default_context(cadata=cert)
  from cryptography import x509
  if not x509.load_pem_x509_certificate(cert.encode()).extensions.get_extension_for_class(x509.BasicConstraints).value.ca:raise ValueError()
  return {'endpoint':endpoint,'credential_id':public['credential_id'],'sha256':public['payload_sha256'],
          'auth_dir':str(Path(settings.data['state_dir'])/'registry-auth'/public['payload_sha256'])}
 except BootstrapError:raise
 except Exception:raise BootstrapError('REGISTRY_ACCESS_INVALID') from None


def content(envelope):
 p=envelope['registration']['registry_access'];endpoint=envelope['manifest']['registry_access']['endpoint']
 auth=base64.b64encode((p['username']+':'+p['password']).encode()).decode()
 return {'auths':{endpoint:{'auth':auth}}}


def _directory(path,private=False,create=False):
 if not path.is_absolute() or path.resolve()!=path:raise BootstrapError('REGISTRY_PATH_UNTRUSTED')
 if create:path.mkdir(parents=True,mode=0o700 if private else 0o755,exist_ok=True)
 try:s=path.lstat()
 except OSError:raise BootstrapError('REGISTRY_ACCESS_NOT_READY') from None
 if not stat.S_ISDIR(s.st_mode) or s.st_uid not in (0,__import__('os').geteuid()) or s.st_mode&(0o077 if private else 0o022):
  raise BootstrapError('REGISTRY_PATH_UNTRUSTED')


def prepare(settings,envelope,trust_root=Path('/etc/docker/certs.d')):
 spec=validate(settings,envelope)
 if not spec:return
 private=Path(spec['auth_dir']);_directory(private.parent,private=True,create=True);_directory(private,private=True,create=True)
 config=private/'config.json'
 if config.exists() or config.is_symlink():
  if json.loads(read_file(config,secret=True))!=content(envelope):raise BootstrapError('REGISTRY_AUTH_CONFLICT')
 else:atomic_json(config,content(envelope))
 # Certificate rotation with a different trust anchor is an explicit maintenance operation.
 _directory(trust_root,create=True);trust=trust_root/spec['endpoint'];_directory(trust,create=True)
 cert=trust/'ca.crt';raw=envelope['registration']['registry_access']['certificate_pem'].encode()
 if cert.exists() or cert.is_symlink():
  if read_file(cert)!=raw:raise BootstrapError('REGISTRY_TRUST_CONFLICT')
 else:
  import os
  fd=os.open(cert,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o644)
  with os.fdopen(fd,'wb') as f:f.write(raw);f.flush();os.fsync(f.fileno())
 status(settings,envelope,trust_root)


def status(settings,envelope,trust_root=Path('/etc/docker/certs.d')):
 spec=validate(settings,envelope)
 if not spec:return
 private=Path(spec['auth_dir']);_directory(private.parent,private=True);_directory(private,private=True)
 if json.loads(read_file(private/'config.json',secret=True))!=content(envelope):raise BootstrapError('REGISTRY_AUTH_CONFLICT')
 _directory(trust_root);_directory(trust_root/spec['endpoint'])
 if read_file(trust_root/spec['endpoint']/'ca.crt')!=envelope['registration']['registry_access']['certificate_pem'].encode():
  raise BootstrapError('REGISTRY_TRUST_CONFLICT')
