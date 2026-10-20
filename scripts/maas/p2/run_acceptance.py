"""Local coordinator for a controlled fault in ONLY the dedicated P2 OCR containers."""
import json,shlex,subprocess,time
from pathlib import Path
root=Path(__file__).resolve().parents[3]
first=['ssh','-S',str(root/'service-validation/ssh-001.sock'),'-o','ProxyCommand=false','root@10.11.31.1']
second=['ssh','-p','16721','-i',str(Path.home()/'.ssh/id_ed25519'),'-o','BatchMode=yes','root@111.4.255.126']
folder='/root/wtaobj/maas-p2/'
paused=[]
def call(cmd,remote):return subprocess.check_output(cmd+[remote],text=True)
def marker(name):return subprocess.run(first+['test -e '+shlex.quote(folder+name)],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL).returncode==0
def mark(name):call(first,'touch '+shlex.quote(folder+name))
def wait(name,process):
    deadline=time.monotonic()+100
    while not marker(name):
        if process.poll() is not None:raise RuntimeError('Acceptance exited before '+name)
        if time.monotonic()>deadline:raise TimeoutError(name)
        time.sleep(.3)
def dedicated(cmd):
    return [n for n in call(cmd,'docker ps --format "{{.Names}}"').splitlines() if n.startswith('maas-ocr-p2-r1-') and n.endswith('-run-0')]
def release():
    for cmd,name in paused:
        subprocess.run(cmd+['docker unpause '+shlex.quote(name)],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
    paused.clear()
for name in ('fault-ready','fault-paused','fault-release','fault-released','fault-stopped','fault-verified'):
    call(first,'rm -f '+shlex.quote(folder+name))
process=subprocess.Popen(first+['python3 /root/wtaobj/maas-framework/scripts/maas/p2/smoke_ocr.py'])
try:
    wait('fault-ready',process)
    for cmd in (first,second):
        names=dedicated(cmd)
        if len(names)!=1:raise RuntimeError('Expected exactly one dedicated OCR instance per node: '+str(names))
        call(cmd,'docker pause '+shlex.quote(names[0]));paused.append((cmd,names[0]))
    mark('fault-paused')
    wait('fault-release',process);release();mark('fault-released')
    wait('fault-stopped',process)
    assert not dedicated(first) and not dedicated(second)
    mark('fault-verified')
    assert process.wait(timeout=50)==0
finally:
    release()
    if process.poll() is None:process.terminate();process.wait(timeout=10)
