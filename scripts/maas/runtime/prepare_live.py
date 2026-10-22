"""001-only persistent pilot bootstrap. Secrets never leave the controller.

Creates an inference-only GPUStack identity, separate from collector/operator keys.
Does not change models, GPUStack DB, workers, Docker daemon or firewall rules.
"""
import argparse,json,os,secrets,shutil,socket,sys,time
from pathlib import Path
ROOT=Path('/root/wtaobj/maas-framework');LIVE=Path('/root/wtaobj/maas-live')
sys.path.insert(0,str(ROOT/'scripts/maas/p0'))
from common import Resources


def private(path,text,uid=0):
    fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
    with os.fdopen(fd,'w') as f:f.write(text);f.flush();os.fsync(f.fileno())
    os.chown(path,uid,uid)


def main():
    if socket.gethostname()!='XZ-31-001' or os.getuid()!=0:raise RuntimeError('001 root only')
    if LIVE.exists():raise RuntimeError('Persistent pilot already exists; do not overwrite its credentials or database configuration')
    for port in (18830,18835):
        with socket.socket() as s:s.bind(('127.0.0.1',port))
    parser=argparse.ArgumentParser(description='Bootstrap from an operator-approved static OCR target')
    parser.add_argument('--revision',required=True);parser.add_argument('--upstream-model',required=True)
    args=parser.parse_args()
    import re
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,127}',args.revision) or not args.upstream_model.strip():
        raise ValueError('Supply an approved revision and upstream model')
    LIVE.mkdir(mode=0o700)
    secret=LIVE/'secrets';secret.mkdir(mode=0o700);os.chown(secret,10001,10001)
    probes=LIVE/'probes';probes.mkdir(mode=0o755)
    shutil.copyfile('/root/wtaobj/service-validation/fixtures/upright.png',probes/'upright.png');(probes/'upright.png').chmod(0o644)
    res=Resources()
    try:
        user,op=res.user('persistent-p4')
        metadata,key=res.key(op,'persistent-p4',[args.upstream_model],expires=30*86400)
        private(secret/'pilot-key',key,10001);private(secret/'pepper',secrets.token_hex(32),10001)
        private(secret/'bindings.json',json.dumps({'credentials':[{'id':'pilot-ocr','project':'pilot-ocr','secret_file':'/run/secrets/pilot-key','models':['ocr.default']}]}),10001)
        catalog={'ocr.default':{'driver':'gpustack.ocr','revision':args.revision,'upstream_model':args.upstream_model,
            'max_runtime_s':25}}
        private(LIVE/'catalog.json',json.dumps(catalog));(LIVE/'catalog.json').chmod(0o644)
        private(LIVE/'app.env','\n'.join(['MAAS_DB_PASSWORD='+secrets.token_hex(32),'MAAS_GPUSTACK_URL=http://127.0.0.1:18080',
            'MAAS_SECRET_DIR='+str(secret),'MAAS_CATALOG_PATH='+str(LIVE/'catalog.json'),
            'MAAS_PROBE_DIR='+str(probes)])+'\n')
        private(LIVE/'identity.json',json.dumps({'user_id':user['id'],'key_id':metadata['id'],'project':'pilot-ocr',
            'created_at':time.time(),'rotation_due_unix':time.time()+29*86400,'key_lifetime_days':30,'resources':res.created}))
    except Exception:
        # Only bootstrap-created credentials are removed; no existing service data.
        res.cleanup()
        raise
    print(json.dumps({'prepared':True,'project':'pilot-ocr','key_lifetime_days':30,'secret_values_printed':False}))


if __name__=='__main__':main()
