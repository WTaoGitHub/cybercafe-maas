"""Persistent LAN registry on 001. No VM/daemon restart; private output stays local."""
import argparse,base64,hashlib,json,os,secrets,socket,ssl,subprocess,urllib.request,urllib.error
from pathlib import Path
ROOT=Path('/root/wtaobj/maas-registry')
OLD=Path('/root/wtaobj/maas-e5-registry')
HOST='10.11.31.1:18443'
NAMES={'store':'maas-registry-store','tls':'maas-registry-tls'}

def run(args,stdin=None,timeout=60):
 p=subprocess.run(args,input=stdin,capture_output=True,text=True,timeout=timeout)
 if p.returncode:raise RuntimeError('Operation failed; inspect private state: '+args[0])
 return p.stdout.strip()

def write(p,data,mode=0o600):
 fd=os.open(p,os.O_CREAT|os.O_EXCL|os.O_WRONLY,mode)
 with os.fdopen(fd,'wb') as f:f.write(data.encode() if isinstance(data,str) else data);f.flush();os.fsync(f.fileno())

def request(path,role=None,method='GET',accept=None):
 headers={}
 if role:
  c=json.loads((ROOT/'credentials.json').read_text())[role]
  headers['Authorization']='Basic '+base64.b64encode((c['username']+':'+c['password']).encode()).decode()
 if accept:headers['Accept']=accept
 ctx=ssl.create_default_context(cafile=str(ROOT/'server.crt'))
 opener=urllib.request.build_opener(urllib.request.ProxyHandler({}),urllib.request.HTTPSHandler(context=ctx))
 try:
  with opener.open(urllib.request.Request('https://'+HOST+path,headers=headers,method=method),timeout=15) as r:return r.status,r.read(),dict(r.headers)
 except urllib.error.HTTPError as e:return e.code,e.read(),dict(e.headers)

def setup():
 assert not ROOT.exists()
 for address,port in [('10.11.31.1',18443),('127.0.0.1',18500)]:
  with socket.socket() as s:s.bind((address,port))
 for name in NAMES.values():assert subprocess.run(['docker','inspect',name],capture_output=True).returncode!=0
 ROOT.mkdir(mode=0o700)
 for name in ['server.crt','server.key']:write(ROOT/name,(OLD/name).read_bytes(),0o644 if name.endswith('crt') else 0o600)
 assert (Path('/etc/docker/certs.d')/HOST/'ca.crt').read_bytes()==(ROOT/'server.crt').read_bytes()
 creds={r:{'username':'maas-'+r,'password':secrets.token_urlsafe(36)} for r in ['reader','publisher']}
 write(ROOT/'credentials.json',json.dumps(creds))
 entries={r:creds[r]['username']+':'+run(['openssl','passwd','-apr1','-stdin'],creds[r]['password']+'\n')+'\n' for r in creds}
 write(ROOT/'readers.htpasswd',entries['reader']+entries['publisher'],0o644)
 write(ROOT/'publishers.htpasswd',entries['publisher'],0o644)
 cfg=(OLD/'nginx.conf').read_text().replace('maas-registry-pilot','maas-registry').replace('worker_connections 128','worker_connections 512').replace('proxy_read_timeout 180s','proxy_read_timeout 600s')
 write(ROOT/'nginx.conf',cfg,0o644);(ROOT/'data').mkdir(mode=0o700)
 images={k:run(['docker','image','inspect','--format','{{.Id}}',tag]) for k,tag in [('store','registry:2'),('tls','nginx:alpine')]}
 run(['docker','create','--name',NAMES['store'],'--restart=no','--memory','512m','--cpus','1',
      '--log-opt','max-size=10m','--log-opt','max-file=3','-p','127.0.0.1:18500:5000',
      '-e','REGISTRY_HTTP_RELATIVEURLS=true','-v',str(ROOT/'data')+':/var/lib/registry',images['store']])
 args=['docker','create','--name',NAMES['tls'],'--restart=no','--network','host','--memory','128m','--cpus','0.5',
       '--log-opt','max-size=10m','--log-opt','max-file=3','-v',str(ROOT/'nginx.conf')+':/etc/nginx/nginx.conf:ro']
 for n in ['server.crt','server.key','readers.htpasswd','publishers.htpasswd']:args+=['-v',str(ROOT/n)+':/etc/nginx/registry/'+n+':ro']
 run(args+[images['tls']])
 for role,name in NAMES.items():
  unit='maas-registry-'+role+'.service';deps='After=docker.service network-online.target\nRequires=docker.service'
  if role=='tls':deps+='\nAfter=maas-registry-store.service\nRequires=maas-registry-store.service'
  text='[Unit]\nDescription=MaaS persistent LAN registry '+role+'\n'+deps+'\n[Service]\nExecStart=/usr/bin/docker start -a '+name+'\nExecStop=/usr/bin/docker stop --time 30 '+name+'\nRestart=on-failure\nRestartSec=5\nTimeoutStopSec=45\n[Install]\nWantedBy=multi-user.target\n'
  write(Path('/etc/systemd/system')/unit,text,0o644)
 run(['systemctl','daemon-reload'])
 run(['systemctl','enable','--now','maas-registry-store.service','maas-registry-tls.service'])
 public={'registry':HOST,'root':str(ROOT),'images':images,'systemd_enabled':True,'old_pilot_unchanged':True,'production_ha':False}
 write(ROOT/'installed.json',json.dumps(public,indent=2));print(json.dumps(public))

def verify():
 import time
 for _ in range(10):
  try:
   assert request('/v2/')[0]==401;break
  except OSError:time.sleep(1)
 codes={'anonymous':request('/v2/')[0],'reader_get':request('/v2/','reader')[0],
        'reader_write':request('/v2/maas/ocr/blobs/uploads/','reader','POST')[0]}
 assert codes=={'anonymous':401,'reader_get':200,'reader_write':401}
 print(json.dumps(codes))

def publish():
 assert not (ROOT/'release.json').exists()
 source='quay.io/cybercafe-maas-ocr:p2-r1';expected='sha256:8eed873554282ec02ba4d8d3a78f8230178cef2ee4f6f94ca6dfb858336e89e5'
 assert run(['docker','image','inspect','--format','{{.Id}}',source])==expected
 build=ROOT/'build';build.mkdir(mode=0o700)
 write(build/'release.json',json.dumps({'release':'ocr-registry-r1','weights_policy':'image-contained'}),0o644)
 write(build/'Dockerfile','FROM '+source+'\nCOPY release.json /opt/maas-distribution-release.json\nLABEL com.cybercafe.maas.distribution=ocr-registry-r1\n',0o644)
 tag=HOST+'/maas/ocr:registry-r1'
 run(['docker','build','--network=none','--pull=false','-t',tag,str(build)],timeout=300)
 image_id=run(['docker','image','inspect','--format','{{.Id}}',tag]);assert image_id!=expected
 auth=ROOT/'publisher-docker';auth.mkdir(mode=0o700)
 c=json.loads((ROOT/'credentials.json').read_text())['publisher']
 run(['docker','--config',str(auth),'login',HOST,'-u',c['username'],'--password-stdin'],c['password']+'\n')
 # Keep the private push log on this host. CLI args never contain a password.
 with (ROOT/'push.private.log').open('w') as f:
  os.chmod(ROOT/'push.private.log',0o600)
  p=subprocess.run(['docker','--config',str(auth),'push',tag],stdout=f,stderr=f,timeout=900)
 assert p.returncode==0
 code,raw,headers=request('/v2/maas/ocr/manifests/registry-r1','reader',accept='application/vnd.docker.distribution.manifest.v2+json, application/vnd.oci.image.manifest.v1+json')
 assert code==200
 manifest=json.loads(raw);digest=headers['Docker-Content-Digest']
 assert digest=='sha256:'+hashlib.sha256(raw).hexdigest() and manifest['config']['digest']==image_id
 value={'canonical_image':HOST+'/maas/ocr@'+digest,'manifest_digest':digest,'image_id':image_id,'source_image_id':expected,
        'new_public_release_layer':True,'weights_policy':'image-contained','registry':HOST,
        'certificate_sha256':hashlib.sha256((ROOT/'server.crt').read_bytes()).hexdigest()}
 write(ROOT/'release.json',json.dumps(value,indent=2));print(json.dumps(value))

if __name__=='__main__':
 assert socket.gethostname()=='XZ-31-001' and os.getuid()==0
 p=argparse.ArgumentParser();p.add_argument('phase',choices=['setup','verify','publish']);a=p.parse_args()
 globals()[a.phase]()
