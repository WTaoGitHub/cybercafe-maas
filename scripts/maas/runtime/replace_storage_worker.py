"""Explicit operator replacement of only the independent new clone's worker.

Run after controller records DRAINED. Preserve the original container stopped and
all native identity bytes. No generic rollback, automatic retry or VM operation.
"""
import hashlib,json,os,socket,subprocess,sys,time
from pathlib import Path
ROOT=Path('/root/wtaobj/e5-storage-v1-install')
BACKUP='maas-gpustack-worker-before-storage-v1-20261026'


def run(args):return subprocess.check_output(args,text=True,stderr=subprocess.PIPE,timeout=90).strip()
def save(path,data):
    with path.open('w') as f:json.dump(data,f,indent=2);f.flush();os.fsync(f.fileno())
    path.chmod(0o600)


def main():
    assert socket.gethostname()=='XZ-31-002' and os.getuid()==0
    assert run(['uptime','-s'])=='2026-10-26 16:49:33'
    assert not (ROOT/'replacement.private.json').exists()
    running=run(['docker','ps','--format','{{.Names}}']).splitlines();assert running==['maas-gpustack-worker']
    assert subprocess.run(['docker','inspect',BACKUP],capture_output=True).returncode!=0
    old=json.loads(run(['docker','inspect','maas-gpustack-worker']))[0]
    assert old['Config']['Labels'].get('com.cybercafe.maas.bootstrap')=='worker-v1'
    sys.path.insert(0,'/opt/maas/lib')
    from maas_bootstrap.client import LocalSettings,atomic_json
    settings=LocalSettings('/etc/maas/bootstrap.json');data=Path(settings.data['worker_data_dir'])
    names=['worker_uuid','worker_name','worker_token','jwt_secret_key']
    before={n:(data/n).read_bytes() for n in names}
    receipt=Path(settings.data['state_dir'])/'maintenance-backups.json';assert not receipt.exists()
    s={'phase':'BEFORE_STOP','old_container':old,'boot':run(['uptime','-s']),
       'docker_pid':run(['systemctl','show','docker','-p','MainPID','--value']),
       'native_hashes':{n:hashlib.sha256(v).hexdigest() for n,v in before.items()}}
    save(ROOT/'replacement.private.json',s)
    run(['docker','stop','--timeout','30','maas-gpustack-worker'])
    run(['docker','rename','maas-gpustack-worker',BACKUP]);s['phase']='OLD_STOPPED';save(ROOT/'replacement.private.json',s)
    atomic_json(receipt,{'schema_version':1,'container_ids':[old['Id']]})
    result=json.loads(run(['/opt/maas/bin/maas-worker-bootstrap','apply','--trigger','manual']))
    assert result['worker_id']==5 and result['worker_name']=='e5-9c5be5b92f10f84b714a5dbe'
    new=json.loads(run(['docker','inspect','maas-gpustack-worker']))[0]
    assert new['Id']!=old['Id'] and new['State']['Running']
    assert all((data/n).read_bytes()==raw for n,raw in before.items())
    repeat=json.loads(run(['/opt/maas/bin/maas-worker-bootstrap','apply','--trigger','manual']))
    assert repeat['code']=='UNCHANGED'
    after=json.loads(run(['docker','inspect','maas-gpustack-worker']))[0]
    assert after['Id']==new['Id'] and after['State']['StartedAt']==new['State']['StartedAt']
    assert s['boot']==run(['uptime','-s']) and s['docker_pid']==run(['systemctl','show','docker','-p','MainPID','--value'])
    s['phase']='REPLACED';save(ROOT/'replacement.private.json',s)
    public={'phase':s['phase'],'old_container_id':old['Id'],'backup_container':BACKUP,'new_container_id':new['Id'],
            'worker_id':5,'identity_files_unchanged':True,'repeat_apply':repeat['code'],
            'layout_version':result['layout_version'],'layout_sha256':result['layout_sha256'],
            'vm_boot_unchanged':True,'docker_pid_unchanged':True,'snapshot_created':False}
    save(ROOT/'replacement.json',public);print(json.dumps(public))


if __name__=='__main__':main()
