"""Trusted host collector; credentials are never supplied to inference processes."""
import argparse,ipaddress,json,logging,os,signal,socket,subprocess,sys,threading,time,urllib.request
from pathlib import Path
from .errors import ServiceError
from .inventory import collect_inventory
from .versioning import VersionPin,verify_pin,register_pin


def atomic_json(path,value):
    path=Path(path);temp=path.with_suffix('.tmp')
    fd=os.open(temp,os.O_CREAT|os.O_TRUNC|os.O_WRONLY,0o600)
    try:
        with os.fdopen(fd,'w') as f:json.dump(value,f,allow_nan=False);f.flush();os.fsync(f.fileno())
        os.replace(temp,path)
        directory=os.open(path.parent,os.O_DIRECTORY)
        try:os.fsync(directory)
        finally:os.close(directory)
    finally:
        temp.unlink(missing_ok=True)


def inventory_command(config):
    host=str(ipaddress.ip_address(config['host']))
    return ['ssh','-F','/dev/null','-i',config['identity_file'],'-o','IdentitiesOnly=yes',
            '-o','BatchMode=yes','-o','StrictHostKeyChecking=yes','-o','UserKnownHostsFile='+config['known_hosts'],
            '-o','ConnectTimeout=3','-o','ClearAllForwardings=yes','-o','ForwardAgent=no',
            '-o','RequestTTY=no','root@'+host,'inventory']


def build_snapshot(pin,control,inventory,gateway,started_at):
    if time.time()-started_at>20:raise ServiceError('COLLECTION_TOO_SLOW','Trusted observation exceeded collection budget')
    workers=[x for x in control['workers'] if x.get('cluster_id')==control['model'].get('cluster_id')]
    if {x['name'] for x in workers}!=set(pin.worker_names) or any(x.get('state')!='ready' or x.get('unreachable',False) for x in workers):
        raise ServiceError('WORKER_UNAVAILABLE','Approved cluster worker set is not ready')
    instances=control['instances']
    if len(instances)!=len(pin.worker_names) or {x['worker_name'] for x in instances}!=set(pin.worker_names) or any(x['state']!='running' for x in instances):
        raise ServiceError('BACKEND_NOT_READY','Approved model instances are not ready')
    ann=gateway['policies']
    if ann.get('higress.io/proxy-next-upstream-tries')!='2' or ann.get('higress.io/proxy-next-upstream')!='error,timeout,http_503,http_502,non_idempotent':
        raise ServiceError('GATEWAY_POLICY_UNVERIFIED','Gateway policy requires review')
    for worker,row in inventory.items():
        if row.get('worker')!=worker or row.get('running_image_ids')!=[pin.image_id] or row.get('paused') is not False:
            raise ServiceError('MODEL_IMAGE_CHANGED','Running content differs from approved inventory')
    verify_pin(pin,control['version'],control['route'],control['targets'],control['model'],control['backend'],inventory)
    return {**{k:control[k] for k in ('version','route','targets','model','backend')},'observed_at':started_at,
            'inventory':inventory,'gateway_max_attempts':3,'gateway_policy':ann,
            'instances':[{k:x[k] for k in ('id','worker_name','worker_ip','port','state')} for x in instances]}


class Collector:
    def __init__(self,config,admin=None):
        self.config=config;self.root=Path(config['root'])
        if admin is None:
            sys.path.insert(0,'/root/wtaobj')
            from gpustack_admin import api
            admin=api
        def bounded_admin(path):
            # The existing operator helper does not set a socket timeout. Limit it
            # in this single-threaded collector so an outage cannot hang refresh.
            previous=socket.getdefaulttimeout();socket.setdefaulttimeout(5)
            try:return admin(path)
            finally:socket.setdefaulttimeout(previous)
        self.admin=bounded_admin

    def observe(self):
        cfg=self.config;ids=json.loads((self.root/'target.json').read_text());start=time.time()
        inventory={}
        for worker,spec in cfg['workers'].items():
            if spec['local']:row=collect_inventory(worker,cfg['image'],cfg['container_prefix'],docker_socket=spec.get('docker_socket'))
            else:row=json.loads(subprocess.check_output(inventory_command(spec),text=True,timeout=10,stderr=subprocess.PIPE))
            inventory[worker]=row
        api=self.admin
        control={'route':api('/v2/model-routes/'+str(ids['route'])),
            'targets':api('/v2/model-route-targets?route_id='+str(ids['route']))['items'],
            'model':api('/v2/models/'+str(ids['model'])),
            'backend':api('/v2/inference-backends/'+str(ids['backend'])),
            'workers':api('/v2/workers')['items'],
            'instances':api('/v2/models/'+str(ids['model'])+'/instances')['items']}
        with urllib.request.urlopen('http://127.0.0.1:18080/version',timeout=5) as response:control['version']=json.load(response)
        code=Path(cfg['gateway_script']).read_text()
        policies=json.loads(subprocess.check_output(['docker','exec','-i','wta-gpustack-server','python','-c',code],text=True,timeout=8,stderr=subprocess.PIPE))
        gateway=next(x for x in policies['ingresses'] if x['name']=='ai-route-route-'+str(ids['route'])+'.internal')
        return control,inventory,gateway,start

    def enroll(self):
        control,inventory,gateway,start=self.observe()
        if (self.root/'pin.json').exists():raise ValueError('Immutable pin already exists')
        pin=register_pin(self.config['revision'],control['route'],control['targets'],control['model'],control['backend'],
                         next(iter(inventory.values()))['image_id'],list(self.config['workers']))
        build_snapshot(pin,control,inventory,gateway,start)
        # Enrollment is explicit; the periodic collector can never overwrite a pin.
        fd=os.open(self.root/'pin.json',os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
        with os.fdopen(fd,'w') as f:json.dump(pin.public(),f);f.flush();os.fsync(f.fileno())
        self.collect_once()

    def collect_once(self):
        snapshot=self.root/'preflight.json'
        try:
            pin=VersionPin(**json.loads((self.root/'pin.json').read_text()))
            control,inventory,gateway,start=self.observe()
            value=build_snapshot(pin,control,inventory,gateway,start)
            atomic_json(snapshot,value)
            atomic_json(self.root/'health.json',{'ok':True,'observed_at':value['observed_at'],'updated_at':time.time()})
            return True
        except Exception as exc:
            # Revoke the previous attestation immediately on observed drift/failure.
            snapshot.unlink(missing_ok=True)
            code=exc.code if isinstance(exc,ServiceError) else 'COLLECTION_UNAVAILABLE'
            atomic_json(self.root/'health.json',{'ok':False,'code':code,'updated_at':time.time()})
            logging.getLogger('maas.collector').warning('collection_failed code=%s',code)
            return False


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--config',required=True)
    parser.add_argument('--enroll',action='store_true');parser.add_argument('--once',action='store_true');args=parser.parse_args()
    path=Path(args.config)
    if path.stat().st_mode & 0o077:raise ValueError('Collector config must be private')
    collector=Collector(json.loads(path.read_text()))
    if args.enroll:collector.enroll();return
    if args.once:raise SystemExit(0 if collector.collect_once() else 1)
    stop=threading.Event()
    for sig in (signal.SIGTERM,signal.SIGINT):signal.signal(sig,lambda *_:stop.set())
    while not stop.is_set():
        collector.collect_once();stop.wait(5)

if __name__=='__main__':main()
