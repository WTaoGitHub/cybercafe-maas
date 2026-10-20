"""Install the private Nginx edge on 001 using its cached, content-pinned image."""
import hashlib,json,shlex,socket,subprocess,time,urllib.request,urllib.error
from pathlib import Path
ROOT=Path('/root/wtaobj/maas-edge');UNIT=Path('/etc/systemd/system/maas-edge.service')
MARKER='# Managed by cybercafe-maas install_edge.py'


def run(args):return subprocess.check_output(args,text=True,stderr=subprocess.PIPE,timeout=45)


def main():
    if socket.gethostname()!='XZ-31-001':raise RuntimeError('Private edge belongs on controller 001')
    if UNIT.exists() and MARKER not in UNIT.read_text():raise RuntimeError('Existing unit is not ours')
    existing=subprocess.run(['docker','inspect','maas-edge'],capture_output=True,text=True)
    if existing.returncode==0:raise RuntimeError('Edge container already exists; inspect before replacing')
    for family,address in ((socket.AF_INET,'127.0.0.1'),(socket.AF_INET6,'::1')):
        with socket.socket(family) as s:s.bind((address,18831))
    image=json.loads(run(['docker','image','inspect','nginx:alpine']))[0]['Id']
    source=Path(__file__).resolve().parents[3]/'deploy/maas/edge.nginx.conf'
    config=source.read_text();ROOT.mkdir(mode=0o755,exist_ok=True)
    path=ROOT/'nginx.conf'
    if path.exists():raise RuntimeError('Prior edge config exists; inspect before replacing')
    path.write_text(config);path.chmod(0o644)
    base=['/usr/bin/docker','run','--rm','--network','host','--read-only','--user','nginx','--cap-drop=ALL',
        '--security-opt=no-new-privileges','--tmpfs','/tmp:rw,nosuid,nodev,size=64m','-v',str(path)+':/etc/nginx/nginx.conf:ro',
        '--entrypoint','nginx',image]
    try:
        run(base+['-t'])
        command=base[:3]+['--name','maas-edge','--label','cybercafe.role=maas-edge']+base[3:]+['-g','daemon off;']
        UNIT.write_text(MARKER+'\n[Unit]\nDescription=Private MaaS allowlisted ingress\nAfter=docker.service\nRequires=docker.service\n[Service]\nRestart=on-failure\nRestartSec=3\nExecStart='+shlex.join(command)+'\nExecStop=/usr/bin/docker stop -t 10 maas-edge\nTimeoutStopSec=20\n[Install]\nWantedBy=multi-user.target\n')
        run(['systemctl','daemon-reload']);run(['systemctl','enable','--now','maas-edge.service'])
        assert run(['systemctl','is-active','maas-edge.service']).strip()=='active'
        for _ in range(40):
            try:urllib.request.urlopen('http://127.0.0.1:18831/',timeout=1).close()
            except urllib.error.HTTPError as exc:
                if exc.code==404 and 'nginx' in exc.headers.get('Server',''):break
            except OSError:pass
            time.sleep(.2)
        else:raise RuntimeError('Private edge did not become ready')
        value={'active':True,'enabled':True,'image_id':image,'config_sha256':hashlib.sha256(config.encode()).hexdigest(),
            'listen':['127.0.0.1:18831','[::1]:18831'],'public_TLS':False}
        (ROOT/'deployment.json').write_text(json.dumps(value,indent=2)+'\n');print(json.dumps(value))
    except BaseException:
        subprocess.run(['systemctl','disable','--now','maas-edge.service'],capture_output=True)
        UNIT.unlink(missing_ok=True);path.unlink(missing_ok=True)
        subprocess.run(['systemctl','daemon-reload'],capture_output=True);raise


if __name__=='__main__':main()
