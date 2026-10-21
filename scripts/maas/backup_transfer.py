"""Local encrypted off-controller backup transport. Recovery key stays on this Mac.
OpenSSL encrypts/decrypts streams; HMAC authenticates ciphertext before restore.
"""
import argparse,hashlib,hmac,json,os,re,secrets,shlex,subprocess
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2];LOCAL_STATE=ROOT/'.maas';STORE=LOCAL_STATE/'backups';KEY=LOCAL_STATE/'backup.key'
SSH=['ssh','-S',str(ROOT/'service-validation/ssh-001.sock'),'-o','ProxyCommand=false','root@10.11.31.1']


def ident(value):
 if not re.fullmatch(r'snapshot-[0-9a-f]{10}',value):raise ValueError('Invalid backup ID')
 return value
def mac(path):
 h=hmac.new(KEY.read_bytes(),digestmod='sha256')
 with path.open('rb') as f:
  for b in iter(lambda:f.read(1024**2),b''):h.update(b)
 return h.hexdigest()
def main():
 p=argparse.ArgumentParser();p.add_argument('action',choices=['fetch','restore-copy']);p.add_argument('id');a=p.parse_args();name=ident(a.id)
 LOCAL_STATE.mkdir(mode=0o700,exist_ok=True);STORE.mkdir(mode=0o700,exist_ok=True)
 if not KEY.exists():
  if a.action!='fetch':raise RuntimeError('Recovery key missing')
  fd=os.open(KEY,os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600)
  with os.fdopen(fd,'w') as f:f.write(secrets.token_hex(32))
 if KEY.stat().st_mode & 0o077:raise RuntimeError('Recovery key must be private')
 path=STORE/(name+'.enc');meta=STORE/(name+'.json')
 if a.action=='fetch':
  if path.exists():raise RuntimeError('Do not overwrite a retained backup')
  folder='/root/wtaobj/maas-live/backups/'+name
  source=subprocess.Popen(SSH+['cat '+shlex.quote(folder+'/snapshot.tar')],stdout=subprocess.PIPE,stderr=subprocess.PIPE)
  with path.open('xb') as out:
   path.chmod(0o600)
   proc=subprocess.run(['openssl','enc','-aes-256-cbc','-pbkdf2','-iter','200000','-salt','-pass','file:'+str(KEY)],stdin=source.stdout,stdout=out,stderr=subprocess.PIPE)
  source.stdout.close();_,err=source.communicate()
  if source.returncode or proc.returncode:raise RuntimeError('Encrypted backup transfer failed')
  status=json.loads(subprocess.check_output(SSH+['cat '+shlex.quote(folder+'/status.json')],text=True))
  meta.write_text(json.dumps({'id':name,'hmac_sha256':mac(path),'archive_sha256':status['archive_sha256'],'encrypted_bytes':path.stat().st_size},indent=2)+'\n');meta.chmod(0o600)
  print(json.dumps({'encrypted_backup':str(path),'recovery_key_printed':False,'bytes':path.stat().st_size}));return
 info=json.loads(meta.read_text())
 if not hmac.compare_digest(info['hmac_sha256'],mac(path)):raise RuntimeError('Backup authentication failed; no restore stream sent')
 folder='/root/wtaobj/maas-restore/'+name
 command='umask 077; mkdir -p '+shlex.quote(folder)+' && cat > '+shlex.quote(folder+'/snapshot.tar')
 decrypt=subprocess.Popen(['openssl','enc','-d','-aes-256-cbc','-pbkdf2','-iter','200000','-pass','file:'+str(KEY),'-in',str(path)],stdout=subprocess.PIPE,stderr=subprocess.PIPE)
 restore=subprocess.run(SSH+[command],stdin=decrypt.stdout,stdout=subprocess.PIPE,stderr=subprocess.PIPE)
 decrypt.stdout.close();_,err=decrypt.communicate()
 if decrypt.returncode or restore.returncode:raise RuntimeError('Restore stream failed')
 actual=subprocess.check_output(SSH+['sha256sum '+shlex.quote(folder+'/snapshot.tar')],text=True).split()[0]
 if actual!=info['archive_sha256']:raise RuntimeError('Restored archive does not match source snapshot')
 print(json.dumps({'restore_folder':folder,'authenticated':True,'plaintext_sha256_matches':True}))


if __name__=='__main__':main()
