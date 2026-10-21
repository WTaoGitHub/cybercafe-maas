"""001 private TLS edge, separate from existing HTTP edge; no public listener."""
import hashlib,json,os,shlex,socket,subprocess
from pathlib import Path
ROOT=Path('/root/wtaobj/maas-private-tls');UNIT=Path('/etc/systemd/system/maas-private-tls.service')
REPO=Path('/root/wtaobj/maas-framework')


def run(*a):return subprocess.check_output(a,text=True,stderr=subprocess.PIPE,timeout=45)
def main():
    if socket.gethostname()!='XZ-31-001':raise RuntimeError('001 only')
    if UNIT.exists() or ROOT.exists():raise RuntimeError('Existing TLS deployment: inspect instead of overwrite')
    with socket.socket() as s:s.bind(('127.0.0.1',18832))
    ROOT.mkdir(mode=0o700)
    run('openssl','req','-x509','-newkey','rsa:3072','-sha256','-nodes','-days','90',
        '-subj','/CN=maas.local','-addext','subjectAltName=DNS:maas.local,IP:127.0.0.1,IP:::1',
        '-keyout',str(ROOT/'server.key'),'-out',str(ROOT/'server.crt'))
    (ROOT/'server.key').chmod(0o600);(ROOT/'server.crt').chmod(0o644)
    original=(REPO/'deploy/maas/edge.nginx.conf').read_text()
    cfg=original.replace('listen 127.0.0.1:18831;','listen 127.0.0.1:18832 ssl;').replace('listen [::1]:18831;','listen [::1]:18832 ssl;')
    cfg=cfg.replace('server_name _;','server_name maas.local;\n        ssl_certificate /tls/server.crt;\n        ssl_certificate_key /tls/server.key;\n        ssl_protocols TLSv1.2 TLSv1.3;\n        ssl_session_tickets off;')
    (ROOT/'nginx.conf').write_text(cfg)
    # Dedicated non-root container user can read only this TLS private key.
    image=json.loads(run('docker','image','inspect','nginx:alpine'))[0]['Id']
    uid=int(run('docker','run','--rm','--entrypoint','id',image,'-u','nginx').strip())
    os.chown(ROOT/'server.key',uid,uid)
    base=['/usr/bin/docker','run','--rm','--network','host','--read-only','--user','nginx','--cap-drop=ALL','--security-opt=no-new-privileges',
        '--tmpfs','/tmp:rw,nosuid,nodev,size=64m','-v',str(ROOT/'nginx.conf')+':/etc/nginx/nginx.conf:ro',
        '-v',str(ROOT)+':/tls:ro','--entrypoint','nginx',image]
    # Mount directory root must be traversable inside the namespace; key remains600.
    ROOT.chmod(0o755)
    run(*base,'-t')
    command=base[:3]+['--name','maas-private-tls']+base[3:]+['-g','daemon off;']
    UNIT.write_text('# Managed by cybercafe-maas install_tls.py\n[Unit]\nDescription=Private TLS MaaS ingress\nAfter=docker.service\nRequires=docker.service\n[Service]\nRestart=on-failure\nRestartSec=3\nExecStart='+shlex.join(command)+'\nExecStop=/usr/bin/docker stop -t 10 maas-private-tls\n[Install]\nWantedBy=multi-user.target\n')
    run('systemctl','daemon-reload');run('systemctl','enable','--now','maas-private-tls.service')
    print(json.dumps({'installed':True,'port':18832,'public':False,'self_signed':True,'image_id':image,
        'certificate_sha256':hashlib.sha256((ROOT/'server.crt').read_bytes()).hexdigest()}))


if __name__=='__main__':main()
