"""One-shot read-only clone evidence; pipe over SSH, never installs/applies anything."""
import hashlib
import json
from pathlib import Path
import socket
import stat
import subprocess


def command(*args):
    return subprocess.check_output(args, text=True, stderr=subprocess.PIPE, timeout=15)


def main():
    cfg = json.loads(Path('/etc/maas/bootstrap.json').read_text())
    state = Path(cfg['state_dir'])
    data = Path(cfg['worker_data_dir'])
    applied = json.loads((state/'applied.json').read_text())
    allowed = ('code','trigger','revision','fingerprint','cluster_id','worker_id','worker_name',
               'native_state','layout_version','layout_sha256')
    worker = json.loads(command('docker','inspect','maas-gpustack-worker'))[0]
    image_id = 'sha256:b46b348685e9c49c2b8b44110a8f7858c02afc3c32bf149037f7f200a84f4a6b'
    image = json.loads(command('docker','image','inspect',image_id))[0]
    ids = command('docker','ps','-q').split()
    rows = json.loads(command('docker','inspect',*ids))
    model_root = cfg.get('model_root','/srv/maas/models')
    cache_root = cfg.get('model_cache_dir','/srv/maas/model-cache')
    containers=[]
    for row in rows:
        if row['Image'] != image_id: continue
        mounts = [{'source':m['Source'],'target':m['Destination'],'rw':m['RW']} for m in row['Mounts']]
        expected = {(model_root,model_root,False),(cache_root,cache_root,True)}
        actual = {(m['source'],m['target'],m['rw']) for m in mounts}
        env_names = [v.split('=',1)[0] for v in row['Config'].get('Env',[])]
        containers.append({'name':row['Name'].lstrip('/'),'id':row['Id'],
            'restart_count':row['RestartCount'],'started_at':row['State']['StartedAt'],
            'mounts':mounts,'only_public_model_cache_mounts':actual==expected,
            'native_token_env_present':any('TOKEN' in name and 'GPUSTACK' in name for name in env_names)})
    logs=[]
    for path in sorted((data/'log/serve').glob('*.log')):
        with path.open('rb') as f:
            f.seek(max(0,path.stat().st_size-256000))
            text=f.read().decode(errors='replace')
        relevant=[line for line in text.splitlines()
                  if ('Pulling image 10.11.31.1:18443/maas/ocr@sha256:' in line
                      or 'Pulled image 10.11.31.1:18443/maas/ocr@sha256:' in line)]
        if relevant:
            logs.append({'file':str(path.relative_to(data)), 'pull_events':relevant[:1]+relevant[-1:]})
    auth=[]
    for path in sorted((state/'registry-auth').glob('*/config.json')):
        value=json.loads(path.read_text())
        auth.append({'endpoints':list(value.get('auths',{})),
                     'file_mode':oct(stat.S_IMODE(path.stat().st_mode)),
                     'directory_mode':oct(stat.S_IMODE(path.parent.stat().st_mode))})
    unit = dict(line.split('=',1) for line in command('systemctl','--no-pager','show',
                'maas-worker-bootstrap.service','-p','ActiveState','-p','SubState','-p','Result').splitlines() if '=' in line)
    events=[]
    for line in command('journalctl','--no-pager','-b','-u','maas-worker-bootstrap.service','-o','cat').splitlines():
        try: value=json.loads(line)
        except ValueError:continue
        if isinstance(value,dict):events.append({k:value[k] for k in allowed if k in value})
    native_uuid=(data/'worker_uuid').read_bytes()
    cert=Path('/etc/docker/certs.d/10.11.31.1:18443/ca.crt')
    result={'hostname':socket.gethostname(),'boot_id':Path('/proc/sys/kernel/random/boot_id').read_text().strip(),
        'ip_addresses':command('hostname','-I').split(),'image_id':cfg['image_id'],
        'applied':{k:applied[k] for k in allowed if k in applied},'boot_events':events,'bootstrap_unit':unit,
        'native_uuid_sha256':hashlib.sha256(native_uuid).hexdigest(),
        'worker_container_id':worker['Id'],'worker_started_at':worker['State']['StartedAt'],
        'worker_restart_count':worker['RestartCount'],
        'gpu':command('nvidia-smi','--query-gpu=name,uuid,driver_version,memory.total,memory.used','--format=csv,noheader').strip(),
        'module_hashes':{name:hashlib.sha256((Path('/opt/maas/lib/maas_bootstrap')/name).read_bytes()).hexdigest()
                         for name in ['client.py','worker.py','registry.py','storage.py']},
        'ocr_image_id':image['Id'],'repo_digests':image.get('RepoDigests'),
        'inference_containers':containers,'native_pull_logs':logs,'registry_auth_metadata':auth,
        'registry_trust_sha256':hashlib.sha256(cert.read_bytes()).hexdigest(),
        'only_read_operations':True,'manual_apply_or_pull':False,
        'cold_cache_before_pull_observed':False}
    print(json.dumps(result,ensure_ascii=False))


if __name__=='__main__': main()
