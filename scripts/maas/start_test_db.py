"""Controller-only disposable test DB; no access to the GPUStack database."""
from pathlib import Path
import os,secrets,subprocess,time
root=Path('/root/wtaobj/maas-framework');private=root/'.secrets';private.mkdir(mode=0o700,exist_ok=True)
password=secrets.token_hex(24)
def write(name,text):
 p=private/name;fd=os.open(p,os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600)
 with os.fdopen(fd,'w') as f:f.write(text)
if not (private/'db.env').exists():
 write('db.env','POSTGRES_USER=maas\nPOSTGRES_DB=maas_test\nPOSTGRES_PASSWORD='+password+'\n')
 write('test.env','MAAS_TEST_DATABASE_URL=postgresql://maas:'+password+'@127.0.0.1:18834/maas_test\n')
name='wta-maas-p1-test-db'
exists=subprocess.run(['docker','inspect',name],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL).returncode==0
if exists:
 import json
 existing=json.loads(subprocess.check_output(['docker','inspect',name]))[0]
 if existing['Config'].get('Labels',{}).get('cybercafe.role')!='p1-disposable-test':raise RuntimeError('Existing container is not our disposable test DB')
 if not existing['State']['Running']:subprocess.run(['docker','start',name],check=True,stdout=subprocess.DEVNULL)
if not exists:
 subprocess.run(['docker','run','-d','--name',name,'--label','cybercafe.role=p1-disposable-test','--env-file',str(private/'db.env'),'-p','127.0.0.1:18834:5432','postgres:16-alpine'],check=True,stdout=subprocess.DEVNULL)
for _ in range(40):
 if subprocess.run(['docker','exec',name,'pg_isready','-U','maas','-d','maas_test'],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL).returncode==0:
  print('Disposable PostgreSQL ready on controller loopback port 18834');break
 time.sleep(.5)
else:raise RuntimeError('Test DB not ready')
