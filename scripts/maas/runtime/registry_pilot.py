"""Isolated HTTPS registry pilot on 001; no daemon, worker or VM restart.

Private credentials stay under ROOT. A public synthetic image is pushed using
the publisher account; pull-only account cannot write. New trust directory is
additive. The registry is a pilot, not a production registry deployment.
"""
import argparse,base64,datetime,hashlib,json,os,secrets,socket,ssl,subprocess,urllib.error,urllib.request
from pathlib import Path
ROOT=Path('/root/wtaobj/maas-e5-registry')
REGISTRY='10.11.31.1:18443'
REPOSITORY='e5/probe'
TAG='pilot-20261026'
FRONT='maas-e5-registry-tls'
BACK='maas-e5-registry-store'


def run(args,stdin=None,timeout=60):
    p=subprocess.run(args,input=stdin,capture_output=True,text=True,timeout=timeout)
    if p.returncode:raise RuntimeError('Command failed: '+args[0]+' '+args[1]+'; inspect private state')
    return p.stdout.strip()


def write(path,text,mode=0o600):
    fd=os.open(path,os.O_CREAT|os.O_EXCL|os.O_WRONLY,mode)
    with os.fdopen(fd,'w') as f:f.write(text);f.flush();os.fsync(f.fileno())


def save(path,data):
    with path.open('w') as f:json.dump(data,f,indent=2);f.flush();os.fsync(f.fileno())
    path.chmod(0o600)


def request(path,account=None,method='GET'):
    headers={}
    if account:
        headers['Authorization']='Basic '+base64.b64encode((account['username']+':'+account['password']).encode()).decode()
    ctx=ssl.create_default_context(cafile=str(ROOT/'server.crt'))
    opener=urllib.request.build_opener(urllib.request.ProxyHandler({}),urllib.request.HTTPSHandler(context=ctx))
    try:
        with opener.open(urllib.request.Request('https://'+REGISTRY+path,headers=headers,method=method),timeout=10) as r:
            return r.status,r.read(),dict(r.headers)
    except urllib.error.HTTPError as e:return e.code,e.read(),dict(e.headers)


def setup():
    if ROOT.exists():raise RuntimeError('Registry pilot directory exists; inspect before retry')
    for name in [FRONT,BACK]:
        if subprocess.run(['docker','inspect',name],capture_output=True).returncode==0:raise RuntimeError('Container name collision')
    for addr,port in [('10.11.31.1',18443),('127.0.0.1',18500)]:
        with socket.socket() as s:s.bind((addr,port))
    run(['docker','image','inspect','registry:2'])
    run(['docker','image','inspect','nginx:alpine'])
    ROOT.mkdir(mode=0o700)
    state={'phase':'PREPARING','registry':REGISTRY,'repository':REPOSITORY,'tag':TAG}
    save(ROOT/'state.json',state)
    baseline={'docker_pid':run(['systemctl','show','docker','-p','MainPID','--value']),
              'boot':run(['uptime','-s'])}
    save(ROOT/'controller-baseline.json',baseline)
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes,serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import NameOID
    import ipaddress
    key=rsa.generate_private_key(public_exponent=65537,key_size=2048)
    subject=x509.Name([x509.NameAttribute(NameOID.COMMON_NAME,'maas-registry-pilot')])
    now=datetime.datetime.now(datetime.timezone.utc)
    cert=(x509.CertificateBuilder().subject_name(subject).issuer_name(subject).public_key(key.public_key())
          .serial_number(x509.random_serial_number()).not_valid_before(now-datetime.timedelta(minutes=5))
          .not_valid_after(datetime.datetime(9999,12,31,tzinfo=datetime.timezone.utc))
          .add_extension(x509.BasicConstraints(ca=True,path_length=0),critical=True)
          .add_extension(x509.SubjectAlternativeName([x509.IPAddress(ipaddress.ip_address('10.11.31.1')),
              x509.IPAddress(ipaddress.ip_address('127.0.0.1')),x509.DNSName('maas-registry.local')]),critical=False)
          .sign(key,hashes.SHA256()))
    write(ROOT/'server.key',key.private_bytes(serialization.Encoding.PEM,serialization.PrivateFormat.PKCS8,serialization.NoEncryption()).decode())
    write(ROOT/'server.crt',cert.public_bytes(serialization.Encoding.PEM).decode(),0o644)
    creds={role:{'username':'e5-'+role,'password':secrets.token_urlsafe(32)} for role in ['reader','publisher']}
    write(ROOT/'credentials.json',json.dumps(creds))
    hashed={role:run(['openssl','passwd','-apr1','-stdin'],c['password']+'\n') for role,c in creds.items()}
    entries={role:creds[role]['username']+':'+hashed[role]+'\n' for role in creds}
    write(ROOT/'readers.htpasswd',entries['reader']+entries['publisher'],0o644)
    write(ROOT/'publishers.htpasswd',entries['publisher'],0o644)
    cfg='''worker_processes 1;
events { worker_connections 128; }
http {
    log_format probe '$remote_addr $request_method $uri $status $remote_user';
    access_log /dev/stdout probe;
    error_log /dev/stderr warn;
    server {
        listen 10.11.31.1:18443 ssl;
        server_name maas-registry.local;
        ssl_certificate /etc/nginx/registry/server.crt;
        ssl_certificate_key /etc/nginx/registry/server.key;
        ssl_protocols TLSv1.2 TLSv1.3;
        server_tokens off;
        client_max_body_size 0;
        allow 10.11.31.0/24;
        allow 127.0.0.1;
        deny all;
        location /v2/ {
            auth_basic "MaaS registry";
            auth_basic_user_file /etc/nginx/registry/readers.htpasswd;
            limit_except GET HEAD OPTIONS {
                auth_basic "MaaS registry publisher";
                auth_basic_user_file /etc/nginx/registry/publishers.htpasswd;
            }
            proxy_pass http://127.0.0.1:18500;
            proxy_set_header Host $http_host;
            proxy_set_header X-Forwarded-Proto https;
            proxy_request_buffering off;
            proxy_buffering off;
            proxy_read_timeout 180s;
        }
        location / { return 404; }
    }
}
'''
    write(ROOT/'nginx.conf',cfg,0o644)
    (ROOT/'data').mkdir(mode=0o700)
    registry_id=run(['docker','image','inspect','--format','{{.Id}}','registry:2'])
    nginx_id=run(['docker','image','inspect','--format','{{.Id}}','nginx:alpine'])
    state.update(registry_image_id=registry_id,nginx_image_id=nginx_id)
    save(ROOT/'state.json',state)
    run(['docker','run','-d','--name',BACK,'--restart=no','--memory','256m','--cpus','0.5',
         '--log-opt','max-size=10m','--log-opt','max-file=2','-p','127.0.0.1:18500:5000',
         '-e','REGISTRY_HTTP_RELATIVEURLS=true','-v',str(ROOT/'data')+':/var/lib/registry',registry_id])
    args=['docker','run','-d','--name',FRONT,'--restart=no','--network','host','--memory','128m','--cpus','0.5',
          '--log-opt','max-size=10m','--log-opt','max-file=2','-v',str(ROOT/'nginx.conf')+':/etc/nginx/nginx.conf:ro']
    for name in ['server.crt','server.key','readers.htpasswd','publishers.htpasswd']:
        args+=['-v',str(ROOT/name)+':/etc/nginx/registry/'+name+':ro']
    run(args+[nginx_id])
    trust=Path('/etc/docker/certs.d')/REGISTRY
    if trust.exists():raise RuntimeError('Existing registry trust must be reviewed')
    trust.mkdir(parents=True,mode=0o755)
    write(trust/'ca.crt',(ROOT/'server.crt').read_text(),0o644)
    state.update(phase='SETUP',certificate_sha256=hashlib.sha256((ROOT/'server.crt').read_bytes()).hexdigest())
    save(ROOT/'state.json',state)
    print(json.dumps({'phase':state['phase'],'registry':REGISTRY,'tls_certificate_sha256':state['certificate_sha256']}))


def publish():
    state=json.loads((ROOT/'state.json').read_text());assert state['phase']=='SETUP'
    creds=json.loads((ROOT/'credentials.json').read_text())
    assert request('/v2/')[0]==401
    assert request('/v2/',creds['reader'])[0]==200
    assert request('/v2/',{'username':creds['reader']['username'],'password':'incorrect-test-password'})[0]==401
    assert request('/v2/e5/probe/blobs/uploads/',creds['reader'],method='POST')[0]==401
    folder=ROOT/'docker-publisher';folder.mkdir(mode=0o700)
    docker=['docker','--config',str(folder)]
    run(docker+['login',REGISTRY,'--username',creds['publisher']['username'],'--password-stdin'],creds['publisher']['password']+'\n')
    source='quay.io/cybercafe-maas-p0:0.1'
    source_id=run(['docker','image','inspect','--format','{{.Id}}',source])
    image=REGISTRY+'/'+REPOSITORY+':'+TAG
    run(['docker','tag',source,image])
    run(docker+['push',image],timeout=180)
    creds['publisher']['password']=''  # Do not include values in metadata below.
    manifest_path='/v2/'+REPOSITORY+'/manifests/'+TAG
    # Registry v2 tag GET defaults to a manifest this Docker client can consume.
    headers={'Authorization':'Basic '+base64.b64encode((creds['reader']['username']+':'+creds['reader']['password']).encode()).decode(),
             'Accept':'application/vnd.docker.distribution.manifest.v2+json, application/vnd.oci.image.manifest.v1+json'}
    ctx=ssl.create_default_context(cafile=str(ROOT/'server.crt'))
    opener=urllib.request.build_opener(urllib.request.ProxyHandler({}),urllib.request.HTTPSHandler(context=ctx))
    with opener.open(urllib.request.Request('https://'+REGISTRY+manifest_path,headers=headers),timeout=10) as r:
        raw=r.read();digest=r.headers['Docker-Content-Digest'];manifest=json.loads(raw)
    assert digest=='sha256:'+hashlib.sha256(raw).hexdigest() and manifest['config']['digest']==source_id
    state.update(phase='PUBLISHED',source_image_id=source_id,manifest_digest=digest,
                 canonical_image=REGISTRY+'/'+REPOSITORY+'@'+digest,
                 anonymous_http=401,incorrect_password_http=401,reader_write_http=401,reader_read_http=200,
                 controller_docker_trust_without_restart=run(['systemctl','show','docker','-p','MainPID','--value'])==json.loads((ROOT/'controller-baseline.json').read_text())['docker_pid'])
    save(ROOT/'state.json',state)
    print(json.dumps(state))


if __name__=='__main__':
    assert socket.gethostname()=='XZ-31-001' and os.getuid()==0
    p=argparse.ArgumentParser();p.add_argument('phase',choices=['setup','publish']);a=p.parse_args()
    if a.phase=='setup':setup()
    else:publish()
