"""Deploy the E5 read-only config endpoint on 001. Never restart existing services.

Sources are uploaded separately; no secrets are printed. Refuses to overwrite a deployment.
"""
import base64, datetime, hashlib, ipaddress, json, os, secrets, shlex, socket, subprocess, sys, time
from pathlib import Path
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa, ed25519
from cryptography.x509.oid import NameOID
ROOT=Path('/root/wtaobj/maas-bootstrap')
CODE=ROOT/'code'
LIVE=ROOT/'private'
OPERATOR=ROOT/'operator'
BOUNDARY=Path('/etc/systemd/system/maas-bootstrap-boundary.service')
UNIT=Path('/etc/systemd/system/maas-bootstrap-config.service')
REPO=Path('/root/wtaobj/maas-framework')

def run(*args,timeout=45):
    return subprocess.check_output(args,text=True,stderr=subprocess.PIPE,timeout=timeout).strip()

def write(path,value,mode=0o600,uid=0):
    fd=os.open(path,os.O_CREAT|os.O_EXCL|os.O_WRONLY,mode)
    with os.fdopen(fd,'w') as f:f.write(value)
    os.chown(path,uid,uid)

def public_state():
    names=('wta-gpustack-server','maas-tasks-api-1','maas-tasks-executor-1','maas-tasks-observer-1','maas-tasks-db-1')
    result={}
    for n in names:
        c=json.loads(run('docker','inspect',n))[0]
        result[n]={'id':c['Id'],'running':c['State']['Running'],'started_at':c['State']['StartedAt']}
    return result

def main():
    if os.getuid()!=0 or socket.gethostname()!='XZ-31-001':raise RuntimeError('001 root only')
    if not CODE.is_dir() or LIVE.exists() or OPERATOR.exists() or UNIT.exists() or BOUNDARY.exists():
        raise RuntimeError('Inspect existing deployment instead of overwriting')
    if not Path('/sys/class/net/enp3s0').exists():raise RuntimeError('Expected LAN interface missing')
    # tar extraction under umask077 may create missing code parents as700. Only source is public.
    (CODE/'maas_bootstrap').chmod(0o755)
    for source in (CODE/'maas_bootstrap').glob('*.py'):source.chmod(0o644)
    with socket.socket() as s:s.bind(('10.11.31.1',18840))
    report={'started_at':time.time(),'protected_before':public_state(),'scope':'E5 endpoint and dedicated INPUT firewall only'}
    sys.path.insert(0,'/root/wtaobj');from gpustack_admin import api
    cluster=api('/v2/clusters/3')
    if cluster['name']!='test1':raise RuntimeError('Expected isolated test1 cluster ID3')
    if any(m.get('cluster_id')==3 for m in api('/v2/models')['items']):raise RuntimeError('test1 must have no models')
    registration=api('/v2/clusters/3/registration-token')
    if not registration.get('token'):raise RuntimeError('Registration token unavailable')
    image=json.loads(run('docker','inspect','maas-tasks-api-1'))[0]['Image']
    worker_image=json.loads(run('docker','image','inspect','quay.io/gpustack/gpustack:v2.2.3'))[0]['RepoDigests'][0]
    LIVE.mkdir(mode=0o700);os.chown(LIVE,10001,10001)
    OPERATOR.mkdir(mode=0o700)
    for d in ('releases','tls'):
        (LIVE/d).mkdir(mode=0o700);os.chown(LIVE/d,10001,10001)
    shared='mbc_'+secrets.token_urlsafe(32)
    write(OPERATOR/'shared-code',shared+'\n')
    now=datetime.datetime.now(datetime.timezone.utc)
    key=rsa.generate_private_key(public_exponent=65537,key_size=3072)
    subject=x509.Name([x509.NameAttribute(NameOID.COMMON_NAME,'maas-bootstrap-test-001')])
    cert=(x509.CertificateBuilder().subject_name(subject).issuer_name(subject).public_key(key.public_key())
          .serial_number(x509.random_serial_number()).not_valid_before(now-datetime.timedelta(minutes=5))
          .not_valid_after(datetime.datetime(9999,12,31,23,59,59,tzinfo=datetime.timezone.utc))
          .add_extension(x509.SubjectAlternativeName([x509.IPAddress(ipaddress.ip_address('10.11.31.1'))]),critical=False)
          .add_extension(x509.BasicConstraints(ca=False,path_length=None),critical=True)
          .add_extension(x509.KeyUsage(digital_signature=True,content_commitment=False,key_encipherment=True,data_encipherment=False,key_agreement=False,key_cert_sign=False,crl_sign=False,encipher_only=False,decipher_only=False),critical=True)
          .add_extension(x509.ExtendedKeyUsage([x509.oid.ExtendedKeyUsageOID.SERVER_AUTH]),critical=False)
          .sign(key,hashes.SHA256()))
    cert_pem=cert.public_bytes(serialization.Encoding.PEM).decode()
    write(LIVE/'tls/server.crt',cert_pem,0o644,10001)
    write(LIVE/'tls/server.key',key.private_bytes(serialization.Encoding.PEM,serialization.PrivateFormat.PKCS8,serialization.NoEncryption()).decode(),uid=10001)
    write(OPERATOR/'server.crt',cert_pem,0o644)
    signing=ed25519.Ed25519PrivateKey.generate()
    write(OPERATOR/'release-signing.key',signing.private_bytes(serialization.Encoding.PEM,serialization.PrivateFormat.PKCS8,serialization.NoEncryption()).decode())
    pub=signing.public_key().public_bytes(serialization.Encoding.PEM,serialization.PublicFormat.SubjectPublicKeyInfo).decode()
    write(OPERATOR/'release-signing.pub',pub,0o644)
    manifest={'schema_version':1,'config_revision':'e5-pilot-r1','site_id':'cybercafe-lab','batch_id':'e5-pilot',
              'cluster_id':3,'cluster_name':'test1','compatible_image_ids':['tower-e5-pilot-v1'],
              'architecture':'x86_64','gpustack_version':'v2.2.3','server_url':'http://10.11.31.1:18080',
              'worker_image_digest':worker_image,'worker_data_dir':'/var/lib/maas-bootstrap/gpustack-worker',
              'worker_name_policy':'native-independent','model_mounts':[],
              'cloud_backup':{'url':'http://configuration.cybercafe.phanlink.cn','enabled':False}}
    canonical=json.dumps(manifest,sort_keys=True,separators=(',',':'),ensure_ascii=False).encode()
    envelope={'manifest':manifest,'manifest_signature':{'algorithm':'Ed25519','key_id':'e5-pilot-signing-v1','value':base64.b64encode(signing.sign(canonical)).decode()},
              'registration':{'cluster_id':3,'token':registration['token']}}
    raw=(json.dumps(envelope,indent=2)+'\n').encode()
    write(LIVE/'releases/e5-pilot-r1.json',raw.decode(),uid=10001)
    write(LIVE/'active.json',json.dumps({'revision':'e5-pilot-r1','sha256':hashlib.sha256(raw).hexdigest()})+'\n',uid=10001)
    policy={'schema_version':1,'bind':'10.11.31.1','port':18840,'interface':'enp3s0','allowed_cidrs':['127.0.0.0/8','10.11.31.0/24'],
            'grants':[{'code_id':'e5-pilot-v1','code_sha256':hashlib.sha256(shared.encode()).hexdigest(),'enabled':True,
                       'expires_at':None,'site_id':'cybercafe-lab','batch_id':'e5-pilot','cluster_id':3}]}
    write(LIVE/'policy.json',json.dumps(policy,indent=2)+'\n',uid=10001)
    # The code directory contains no credentials; --read-only, no Docker socket or GPUs.
    boundary_command=['/usr/bin/python3',str(CODE/'bootstrap_boundary.py'),'--policy',str(LIVE/'policy.json')]
    write(BOUNDARY,'[Unit]\nDescription=E5 configuration port boundary\nAfter=network-online.target\nBefore=maas-bootstrap-config.service\n[Service]\nType=oneshot\nRemainAfterExit=yes\nExecStart='+shlex.join(boundary_command)+'\n[Install]\nWantedBy=multi-user.target\n',0o644)
    argv=['/usr/bin/docker','run','--rm','--name','maas-bootstrap-config','--network','host','--read-only','--user','10001:10001',
          '--cap-drop=ALL','--security-opt=no-new-privileges','--pids-limit','128','--memory','256m','--cpus','1',
          '--log-driver','local','--log-opt','max-size=5m','--log-opt','max-file=2',
          '-e','PYTHONDONTWRITEBYTECODE=1','-e','MAAS_BOOTSTRAP_STATE=/run/bootstrap',
          '-v',str(CODE/'maas_bootstrap')+':/app/maas_bootstrap:ro','-v',str(LIVE)+':/run/bootstrap:ro',
          image,'uvicorn','maas_bootstrap.service:create_app','--factory','--host','10.11.31.1','--port','18840',
          '--ssl-certfile','/run/bootstrap/tls/server.crt','--ssl-keyfile','/run/bootstrap/tls/server.key',
          '--ssl-ciphers','ECDHE+AESGCM:ECDHE+CHACHA20','--no-access-log','--no-proxy-headers','--limit-concurrency','128','--timeout-keep-alive','5']
    write(UNIT,'[Unit]\nDescription=E5 private LAN HTTPS configuration API\nAfter=docker.service maas-bootstrap-boundary.service\nRequires=docker.service maas-bootstrap-boundary.service\n[Service]\nRestart=on-failure\nRestartSec=5\nTimeoutStopSec=20\nExecStart='+shlex.join(argv)+'\nExecStop=/usr/bin/docker stop -t 10 maas-bootstrap-config\n[Install]\nWantedBy=multi-user.target\n',0o644)
    run('systemctl','daemon-reload')
    run('systemctl','enable','--now','maas-bootstrap-boundary.service')
    run('systemctl','enable','--now','maas-bootstrap-config.service')
    import ssl,urllib.request
    context=ssl.create_default_context(cafile=str(OPERATOR/'server.crt'))
    deadline=time.monotonic()+30
    while True:
        try:
            with urllib.request.urlopen('https://10.11.31.1:18840/healthz',context=context,timeout=3) as h:
                if h.status==200:break
        except Exception:
            if time.monotonic()>deadline:raise RuntimeError('Bootstrap readiness failed')
            time.sleep(1)
    req=urllib.request.Request('https://10.11.31.1:18840/bootstrap/v1/config?image_id=tower-e5-pilot-v1',headers={'Authorization':'Bearer '+shared})
    with urllib.request.urlopen(req,context=context,timeout=5) as h:
        received=json.load(h);assert received==envelope and h.headers['Cache-Control']=='no-store'
    signing.public_key().verify(base64.b64decode(received['manifest_signature']['value']),canonical)
    report.update(protected_after=public_state(),port=18840,bind='10.11.31.1',allowed_cidrs=policy['allowed_cidrs'],
                  certificate_not_after=cert.not_valid_after.isoformat()+'Z',certificate_sha256=cert.fingerprint(hashes.SHA256()).hex(),
                  release_signing_private_key_not_in_container=True,revision='e5-pilot-r1',test_cluster_id=3,health_status=200,authenticated_read=True,
                  manifest_signature_verified=True,code_id='e5-pilot-v1',shared_code_expires_at=None,
                  signing_public_key_sha256=hashlib.sha256(pub.encode()).hexdigest(),image_id=image,
                  native_registration_path_verified=False,finished_at=time.time())
    assert report['protected_before']==report['protected_after']
    report['ok']=True
    write(ROOT/'deployment.json',json.dumps(report,indent=2)+'\n')
    print(json.dumps(report))
if __name__=='__main__':main()
