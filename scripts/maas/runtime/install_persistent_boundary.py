"""Install owned boot/runtime units; never restart Docker implicitly."""
import json,subprocess
from pathlib import Path
ROOT=Path('/root/wtaobj/maas-framework')

def run(*a):return subprocess.check_output(a,text=True,stderr=subprocess.PIPE,timeout=90)
def main():
    run('python3',str(ROOT/'scripts/maas/runtime/persist_boundary.py'),'install')
    for name in ('maas-boundary-boot.service','maas-boundary-refresh.service','maas-boundary-refresh.timer'):
        dest=Path('/etc/systemd/system')/name
        if dest.exists():raise RuntimeError('Existing persistent unit requires review')
        dest.write_text((ROOT/'deploy/maas/systemd'/name).read_text())
    drop=Path('/etc/systemd/system/docker.service.d/maas-boundary.conf');drop.parent.mkdir(parents=True,exist_ok=True)
    if drop.exists():raise RuntimeError('Docker boundary drop-in already exists')
    wants=['maas-boundary-refresh.service']+[n for n in ('maas-edge.service','maas-private-tls.service') if (Path('/etc/systemd/system')/n).exists()]
    drop.write_text('[Unit]\nRequires=maas-boundary-boot.service\nAfter=maas-boundary-boot.service\nWants='+' '.join(wants)+'\n')
    run('systemctl','daemon-reload')
    run('systemd-analyze','verify','maas-boundary-boot.service','maas-boundary-refresh.service')
    run('systemctl','enable','maas-boundary-boot.service','maas-boundary-refresh.timer')
    run('systemctl','start','maas-boundary-boot.service');run('systemctl','start','maas-boundary-refresh.service','maas-boundary-refresh.timer')
    print(run('python3',str(ROOT/'scripts/maas/runtime/persist_boundary.py'),'status').strip())
if __name__=='__main__':main()
