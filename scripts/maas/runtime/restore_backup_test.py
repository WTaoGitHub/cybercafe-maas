"""Restore authenticated copy into disposable test DB and private directory only."""
import argparse,hashlib,json,os,re,shutil,subprocess,sys,tarfile,time,uuid
from pathlib import Path
ROOT=Path('/root/wtaobj/maas-framework');sys.path.insert(0,str(ROOT))
from maas_platform.backup_validation import validate_members


def sha(path):
 h=hashlib.sha256()
 with path.open('rb') as f:
  for b in iter(lambda:f.read(1024**2),b''):h.update(b)
 return h.hexdigest()
def run(*a):return subprocess.check_output(a,text=True,stderr=subprocess.PIPE,timeout=120)
def main():
 p=argparse.ArgumentParser();p.add_argument('id');a=p.parse_args()
 if not re.fullmatch(r'snapshot-[0-9a-f]{10}',a.id):raise ValueError('Invalid snapshot ID')
 folder=Path('/root/wtaobj/maas-restore')/a.id;start=time.monotonic()
 with tarfile.open(folder/'snapshot.tar') as t:
  validate_members(t.getmembers(),{'database.dump','artifacts.tar','config.tar','manifest.json'});t.extractall(folder,filter='data')
 manifest=json.loads((folder/'manifest.json').read_text())
 for name,meta in manifest['files'].items():assert sha(folder/name)==meta['sha256']
 for name,dest in [('artifacts.tar','artifacts'),('config.tar','config')]:
  target=folder/dest;target.mkdir(mode=0o700)
  with tarfile.open(folder/name) as t:validate_members(t.getmembers());t.extractall(target,filter='data')
 secret=folder/'config/secrets'
 for path in [secret,*secret.iterdir()]:os.chown(path,10001,10001)
 # Only this restored artifact tree is exposed to the verifier's UID10001.
 for base,dirs,files in os.walk(folder/'artifacts'):
  os.chown(base,10001,10001)
  for name in files:os.chown(Path(base)/name,10001,10001)
 label=run('docker','inspect','--format','{{index .Config.Labels "cybercafe.role"}}','wta-maas-p1-test-db').strip()
 assert label=='p1-disposable-test'
 db='restore_'+uuid.uuid4().hex[:10]+'_test';created=False
 try:
  run('docker','exec','wta-maas-p1-test-db','createdb','-U','maas',db);created=True
  with (folder/'database.dump').open('rb') as f:subprocess.run(['docker','exec','-i','wta-maas-p1-test-db','pg_restore','-U','maas','-d',db],stdin=f,stderr=subprocess.PIPE,check=True,timeout=120)
  code="""import hashlib,json,os
from pathlib import Path
import psycopg
from psycopg.rows import dict_row
url=os.environ['MAAS_TEST_DATABASE_URL'].rsplit('/',1)[0]+'/'+os.environ['RESTORE_DB']
with psycopg.connect(url,row_factory=dict_row) as c:
 artifacts=c.execute('SELECT * FROM artifacts WHERE deleted_at IS NULL').fetchall()
 tasks=c.execute('SELECT state,count(*) n FROM tasks GROUP BY state').fetchall()
 usage=c.execute('SELECT count(*) n FROM task_usage_events').fetchone()['n']
 for a in artifacts:
  raw=(Path('/restore')/a['id']).read_bytes();assert len(raw)==a['size'] and hashlib.sha256(raw).hexdigest()==a['sha256']
 assert c.execute("SELECT count(*) n FROM tasks WHERE state='SUCCEEDED'").fetchone()['n']==usage
 for name in ('pilot-key','pepper','bindings.json'):assert (Path('/run/secrets')/name).read_bytes()
 print(json.dumps({'artifacts_verified':len(artifacts),'tasks':tasks,'successful_task_events':usage,'database_restored':True,'live_database_modified':False}))
"""
  result=json.loads(run('docker','run','--rm','--network','host','--env-file',str(ROOT/'.secrets/test.env'),'-e','RESTORE_DB='+db,
      '-v',str(folder/'artifacts')+':/restore:ro','-v',str(secret)+':/run/secrets:ro','cybercafe-maas-tasks:p4-r1','python','-c',code))
  # Config restored privately and checked without printing credential values.
  config=folder/'config';assert (config/'secrets/pilot-key').stat().st_size>0 and (config/'app.env').is_file()
  result.update(config_archive_restored=True,restore_seconds=round(time.monotonic()-start,3),snapshot_at=manifest['snapshot_at'],ok=True)
  report=Path('/root/wtaobj/maas-live/p4-ops/restore-result.json');report.write_text(json.dumps(result,indent=2)+'\n');print(json.dumps(result))
 finally:
  if created:run('docker','exec','wta-maas-p1-test-db','dropdb','-U','maas',db)
  shutil.rmtree(folder)  # Delete only this disposable restore tree, including its secret copy.


if __name__=='__main__':main()
