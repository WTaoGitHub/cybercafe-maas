"""Temporary local test collector. Missing refresh fails closed after 30 seconds.
Production needs an always-on trusted collector; this command is not that deployment.
"""
import os
import argparse,json,shlex,subprocess,tempfile,time
from pathlib import Path
p=argparse.ArgumentParser();p.add_argument('--loop',action='store_true');a=p.parse_args()
root=Path(__file__).resolve().parents[3]
ssh=['ssh','-S',str(root/'service-validation/ssh-001.sock'),'-o','ProxyCommand=false','root@10.11.31.1']
while True:
    with tempfile.TemporaryDirectory() as folder:
        path=Path(folder)/'inventory.json'
        subprocess.run(['python3',str(root/'scripts/maas/p0/collect_image_inventory.py'),'--image','quay.io/cybercafe-maas-ocr:p2-r1','--output',str(path)],check=True,stdout=subprocess.DEVNULL)
        inventory=json.loads(path.read_text())
        second=['ssh','-F',os.environ.get('MAAS_SSH_CONFIG',str(Path(__file__).resolve().parents[2] / '.maas/ssh/config')),'-o','BatchMode=yes',os.environ.get('MAAS_LEGACY_WORKER_SSH_ALIAS','maas-legacy-worker')]
        runtime_ok=True
        for name,command in [('XZ-31-001',ssh),('XZ-31-002',second)]:
            names=subprocess.check_output(command+['docker ps --format "{{.Names}}"'],text=True).splitlines()
            names=[n for n in names if n.startswith('maas-ocr-p2-r1-') and n.endswith('-run-0')]
            if len(names)!=1:
                runtime_ok=False;break
            row=json.loads(subprocess.check_output(command+['docker inspect '+shlex.quote(names[0])],text=True))[0]
            inventory[name]['running_image_ids']=[row['Image']]
            inventory[name]['paused']=row['State']['Paused']
        if not runtime_ok:
            print('Dedicated runtime unavailable; preflight not refreshed',flush=True)
            if not a.loop:raise SystemExit(1)
            time.sleep(10);continue
        result=subprocess.run(ssh+['python3 /root/wtaobj/maas-framework/scripts/maas/p2/control_snapshot.py'],input=json.dumps(inventory).encode(),stdout=subprocess.PIPE,stderr=subprocess.PIPE)
        if result.returncode:
            print('Preflight refresh unavailable; previous metadata will expire',flush=True)
            if not a.loop:raise SystemExit(1)
        else:print(result.stdout.decode().strip(),flush=True)
    if not a.loop:break
    time.sleep(10)
