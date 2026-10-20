"""Local bootstrap for the retained controller service and forced-command inventory key.
The new private key is generated on 001 and never leaves it. Existing keys are preserved.
"""
import json,shlex,subprocess
from pathlib import Path
root=Path(__file__).resolve().parents[3]
first=['ssh','-S',str(root/'service-validation/ssh-001.sock'),'-o','ProxyCommand=false','root@10.11.31.1']
second=['ssh','-p','16721','-i',str(Path.home()/'.ssh/id_ed25519'),'-o','BatchMode=yes','root@111.4.255.126']
def call(command,code,data=None):
    return subprocess.check_output(command+['python3 -c '+shlex.quote(code)],input=None if data is None else json.dumps(data).encode()).decode()
public=call(first,"""
from pathlib import Path
import subprocess
root=Path('/root/wtaobj/maas-runtime');root.mkdir(mode=0o700,exist_ok=True)
key=root/'inventory_ed25519'
if not key.exists():subprocess.run(['ssh-keygen','-q','-t','ed25519','-N','','-C','maas-inventory-pilot-20261020','-f',str(key)],check=True)
key.chmod(0o600)
print(key.with_suffix('.pub').read_text().strip())
""").strip()
host_public=subprocess.check_output(second+['cat /etc/ssh/ssh_host_ed25519_key.pub'],text=True).strip()
call(second,"""
import json,os,sys
from pathlib import Path
body=json.load(sys.stdin)
root=Path('/root/wtaobj/maas-inventory');root.mkdir(mode=0o700,exist_ok=True)
p=root/'config.json';p.write_text(json.dumps(body['config']));p.chmod(0o600)
ssh=Path('/root/.ssh');ssh.mkdir(mode=0o700,exist_ok=True)
auth=ssh/'authorized_keys';previous=auth.read_text() if auth.exists() else ''
line='from="10.11.31.1",restrict,command="/usr/bin/python3 /root/wtaobj/maas-framework/scripts/maas/runtime/inventory_agent.py" '+body['public']
owned=[x for x in previous.splitlines() if 'maas-inventory-pilot-20261020' in x]
if owned and owned!=[line]:raise RuntimeError('Existing inventory key registration differs')
if not owned:
    with auth.open('a') as f:
        if previous and not previous.endswith(chr(10)):f.write(chr(10))
        f.write(line+chr(10))
auth.chmod(0o600)
""",{'public':public,'config':{'worker':'XZ-31-002','image':'quay.io/cybercafe-maas-ocr:p2-r1','container_prefix':'maas-ocr-p2-r2'}})
call(first,"""
import json,sys
from pathlib import Path
body=json.load(sys.stdin);root=Path('/root/wtaobj/maas-runtime')
p=root/'known_hosts';p.write_text('10.11.31.2 '+body['host']+chr(10));p.chmod(0o600)
config={'root':str(root),'revision':'ocr-p2-r2','image':'quay.io/cybercafe-maas-ocr:p2-r1',
 'container_prefix':'maas-ocr-p2-r2','gateway_script':'/root/wtaobj/maas-framework/scripts/maas/p0/inspect_gateway.py',
 'workers':{'XZ-31-001':{'local':True},'XZ-31-002':{'local':False,'host':'10.11.31.2',
 'identity_file':str(root/'inventory_ed25519'),'known_hosts':str(p)}}}
p=root/'collector.json';p.write_text(json.dumps(config));p.chmod(0o600)
""",{'host':host_public})
print('Controller-only inventory private key and pinned-host forced-command access installed')
