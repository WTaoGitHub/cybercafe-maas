"""Run locally after creating the p2-r1 archive on 001. No SSH keys are copied."""
from pathlib import Path
import shlex,subprocess
root=Path(__file__).resolve().parents[3]
first=['ssh','-S',str(root/'service-validation/ssh-001.sock'),'-o','ProxyCommand=false','root@10.11.31.1']
second=['ssh','-p','16721','-i',str(Path.home()/'.ssh/id_ed25519'),'-o','BatchMode=yes','root@111.4.255.126']
server=subprocess.Popen(first+['python3 /root/wtaobj/maas-framework/scripts/maas/p2/serve_image.py'],stdout=subprocess.PIPE,text=True)
try:
    url=server.stdout.readline().strip()
    if not url.startswith('http://10.11.31.1:18839/'):raise RuntimeError('Image server did not start')
    pipeline="curl --noproxy '*' -fsS "+shlex.quote(url)+' | gzip -dc | docker load'
    subprocess.run(second+['bash -o pipefail -c '+shlex.quote(pipeline)],check=True)
    assert server.wait(timeout=15)==0
    ids=[subprocess.check_output(cmd+['docker image inspect quay.io/cybercafe-maas-ocr:p2-r1 --format "{{.Id}}"'],text=True).strip() for cmd in (first,second)]
    assert ids[0]==ids[1]
    print('Both workers have the exact same dedicated image:',ids[0])
finally:
    if server.poll() is None:server.terminate();server.wait(timeout=10)
