"""Stop only the existing H3 and its verified tunnel; retain containers/images/volume."""
import json,os,socket,subprocess,time
from pathlib import Path
sysroot=Path('/root/wtaobj/maas-h3-parked')


def run(args):return subprocess.check_output(args,text=True,stderr=subprocess.PIPE,timeout=30)
def inspect(name):return json.loads(run(['docker','inspect',name]))[0]
def manifest():
    volume=json.loads(run(['docker','volume','inspect','h3-weights']))[0]
    root=Path(volume['Mountpoint'])
    return sorted([{'path':str(p.relative_to(root)),'bytes':p.stat().st_size} for p in root.rglob('*') if p.is_file()],key=lambda x:x['path'])


def main():
    if socket.gethostname() not in ('XZ-31-001','XZ-31-002'):raise RuntimeError('Existing test workers only')
    h3=inspect('cybercafe-h3');tunnel=inspect('cloudflared')
    args=(tunnel['Config'].get('Entrypoint') or [])+(tunnel['Config'].get('Cmd') or [])
    if '--url' not in args or args[args.index('--url')+1]!='http://127.0.0.1:11435':
        raise RuntimeError('Refusing to stop a tunnel that does not target H3')
    if not any(m.get('Name')=='h3-weights' and m['Destination']=='/models' for m in h3['Mounts']):
        raise RuntimeError('Unexpected model volume')
    sysroot.mkdir(mode=0o700,exist_ok=True)
    before={'node':socket.gethostname(),'observed_at':time.time(),'models':manifest(),'containers':{}}
    for name,c in [('cybercafe-h3',h3),('cloudflared',tunnel)]:
        before['containers'][name]={'id':c['Id'],'image':c['Image'],'pid':c['State']['Pid'],'restart':c['HostConfig']['RestartPolicy']}
    path=sysroot/'before.json'
    if not path.exists():
        fd=os.open(path,os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600)
        with os.fdopen(fd,'w') as f:json.dump(before,f,indent=2)
    for name in ('cloudflared','cybercafe-h3'):
        run(['docker','stop','-t','5',name]);run(['docker','update','--restart=no',name])
    result={'node':socket.gethostname(),'stopped_at':time.time(),'containers':{},'model_files_preserved':manifest()==before['models']}
    for name in ('cybercafe-h3','cloudflared'):
        c=inspect(name);original=before['containers'][name]
        assert c['Id']==original['id'] and c['Image']==original['image'] and not c['State']['Running']
        assert c['HostConfig']['RestartPolicy']['Name']=='no'
        if original['pid']:assert not Path('/proc/'+str(original['pid'])).exists()
        run(['docker','image','inspect',original['image']])
        result['containers'][name]={'running':False,'restart':'no','container_preserved':True,'image_preserved':True}
    assert result['model_files_preserved']
    with socket.socket() as s:
        s.settimeout(1);result['port_11435_closed']=s.connect_ex(('127.0.0.1',11435))!=0
    assert result['port_11435_closed']
    (sysroot/'result.json').write_text(json.dumps(result,indent=2)+'\n');print(json.dumps(result))


if __name__=='__main__':main()
