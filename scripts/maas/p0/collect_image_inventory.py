"""Local trusted operator inventory. No key contents are read or forwarded."""
import argparse,json,re,shlex,subprocess,time
from pathlib import Path
p=argparse.ArgumentParser();p.add_argument('--image',required=True);p.add_argument('--output',type=Path,required=True);a=p.parse_args()
if not re.fullmatch(r'[A-Za-z0-9_./:@-]+',a.image):raise SystemExit('Invalid image reference')
root=Path(__file__).resolve().parents[3]
commands={'XZ-31-001':['ssh','-S',str(root/'service-validation/ssh-001.sock'),'-o','ProxyCommand=false','root@10.11.31.1'],
          'XZ-31-002':['ssh','-p','16721','-i',str(Path.home()/'.ssh/id_ed25519'),'-o','BatchMode=yes','root@111.4.255.126']}
result={}
for name,command in commands.items():
 ident=subprocess.check_output(command+[shlex.join(['docker','image','inspect','--format','{{.Id}}',a.image])],text=True).strip()
 if not re.fullmatch(r'sha256:[a-f0-9]{64}',ident):raise SystemExit('Image is missing or ambiguous')
 result[name]={'image_name':a.image,'image_id':ident,'observed_at':time.time()}
a.output.write_text(json.dumps(result,indent=2)+'\n');print('Trusted image inventory written; workers:',len(result))
