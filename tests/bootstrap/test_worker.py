import json,os
from pathlib import Path
import pytest
from maas_bootstrap.client import BootstrapError,LocalState,atomic_json
from maas_bootstrap.worker import apply,NativeDockerWorker,guard_clone_host
from test_client import setup,Fetcher,TOKEN

class FakeWorker:
    def __init__(self,confirm=True):self.container=None;self.creates=0;self.starts=0;self.confirm=confirm
    def guard(self):pass
    def inspect(self):return self.container
    def check_environment(self,s,e):pass
    def create(self,s,e,m,i,env):
        self.creates+=1;self.container={'running':False,'fingerprint':m['fingerprint']}
        self.identity=i
        assert Path(env).stat().st_mode&0o777==0o600
        assert 'GPUSTACK_TOKEN='+TOKEN in Path(env).read_text()
    def start(self):self.starts+=1;self.container['running']=True
    def registered(self,s,e,i):
        if not self.confirm:return None
        return {'worker_id':42,'worker_name':i['worker_name'],'cluster_id':3,'native_state':'ready'}

def test_first_apply_then_manual_unchanged_and_process_recovery(setup):
    s,sign=setup;runtime=FakeWorker();f=Fetcher(sign())
    a=apply(s,'boot',f,runtime);b=apply(s,'manual',f,runtime)
    assert a['code']=='APPLIED' and b['code']=='UNCHANGED' and a['worker_name']==b['worker_name']
    assert f.calls==2 and runtime.creates==runtime.starts==1
    runtime.container['running']=False
    c=apply(s,'manual',f,runtime);assert c['code']=='APPLIED' and runtime.creates==1 and runtime.starts==2
    assert TOKEN not in json.dumps(LocalState(s.data['state_dir']).status())

def test_unknown_registration_retry_never_creates_second_worker(setup):
    s,sign=setup;r=FakeWorker(confirm=False)
    for _ in range(2):
        with pytest.raises(BootstrapError,match='REGISTRATION_UNCONFIRMED'):apply(s,fetcher=Fetcher(sign()),backend=r,timeout=0)
    assert r.creates==r.starts==1 and LocalState(s.data['state_dir']).read('applied.json') is None
    r.confirm=True;assert apply(s,fetcher=Fetcher(sign()),backend=r)['code']=='APPLIED'

def test_changed_config_needs_maintenance_even_if_container_stopped(setup):
    s,sign=setup;r=FakeWorker();apply(s,fetcher=Fetcher(sign()),backend=r)
    m={**sign()['manifest'],'config_revision':'r2'}
    for running in (True,False):
        r.container['running']=running
        with pytest.raises(BootstrapError,match='NEEDS_MAINTENANCE'):apply(s,fetcher=Fetcher(sign(m)),backend=r)
    assert r.creates==r.starts==1

def test_dirty_native_directory_is_not_adopted_or_erased(setup):
    s,sign=setup;data=Path(s.data['worker_data_dir']);data.mkdir();(data/'worker_token').write_text('old-state')
    r=FakeWorker()
    with pytest.raises(BootstrapError,match='NATIVE_IDENTITY_WITHOUT_LOCAL_OWNER'):apply(s,fetcher=Fetcher(sign()),backend=r)
    assert (data/'worker_token').read_text()=='old-state' and r.creates==0

def test_protected_nodes_and_mother_cannot_launch(monkeypatch):
    import maas_bootstrap.worker as w
    for host in ('XZ-31-001','xz-31-001','tower','TOWER','tower-mNST8iPdv','tower-8CbKAhaaf','ToWeR-new-template'):
        monkeypatch.setattr(w.platform,'node',lambda:host)
        with pytest.raises(BootstrapError,match='PROTECTED_HOST_WORKER_FORBIDDEN'):guard_clone_host()

def test_same_manifest_different_local_clones_have_distinct_names(setup,tmp_path):
    from maas_bootstrap.client import LocalSettings
    a,sign=setup;r1=FakeWorker();first=apply(a,fetcher=Fetcher(sign()),backend=r1)
    d={**a.data,'state_dir':str(tmp_path/'another-state')};p=tmp_path/'another.json';atomic_json(p,d)
    r2=FakeWorker();second=apply(LocalSettings(p),fetcher=Fetcher(sign()),backend=r2)
    assert first['worker_name']!=second['worker_name']


def test_native_probe_matches_local_identity_and_exact_cluster(setup,monkeypatch):
    import maas_bootstrap.worker as w
    s,sign=setup;data=Path(s.data['worker_data_dir']);data.mkdir(mode=0o700)
    identity={'worker_name':'e5-local-test','cluster_id':3}
    for name,value in {'worker_token':'individual-native-token','worker_uuid':'native-uuid','worker_name':identity['worker_name']}.items():
        p=data/name;p.write_text(value);p.chmod(0o600)
    row={'id':12,'name':identity['worker_name'],'worker_uuid':'native-uuid','cluster_id':3,'state':'ready'}
    rows=[row]
    class Response:
        def __enter__(self):return self
        def __exit__(self,*a):pass
        def read(self,n):return json.dumps({'items':rows}).encode()
    class Opener:
        def open(self,req,timeout):
            assert req.full_url=='http://10.11.31.1:18080/v2/workers?me=true'
            assert req.get_header('Authorization')=='Bearer individual-native-token'
            return Response()
    monkeypatch.setattr(w.urllib.request,'build_opener',lambda *a:Opener())
    probe=NativeDockerWorker()
    assert probe.registered(s,sign(),identity)['worker_id']==12
    row['cluster_id']=1;assert probe.registered(s,sign(),identity) is None
    row['cluster_id']=3;row['worker_uuid']='other';assert probe.registered(s,sign(),identity) is None
    row['worker_uuid']='native-uuid';rows.append(dict(row));assert probe.registered(s,sign(),identity) is None


def test_docker_create_pins_image_and_never_puts_token_in_arguments(setup,monkeypatch,tmp_path):
    import maas_bootstrap.worker as w
    s,sign=setup;calls=[];e=sign()
    monkeypatch.setattr(w,'run',lambda args,**kw:calls.append(args))
    NativeDockerWorker().create(s,e,{'fingerprint':'a'*64},{},tmp_path/'private-env')
    args=calls[0]
    assert args[:2]==['docker','create'] and args[-1]==e['manifest']['worker_image_digest']
    assert '--restart=no' in args and '--env-file' in args
    assert TOKEN not in ' '.join(args) and '--server-url' not in args


@pytest.mark.parametrize('hostname,addresses', [
    ('XZ-31-002',['10.11.31.2']),
    ('XZ-31-002',['10.11.31.15']),
    ('new-clone',['10.11.31.2']),
    ('towerish-worker',['10.11.31.15']),
    ('worker-tower',['10.11.31.15']),
])
def test_replacement_clone_can_reuse_002_name_or_address(monkeypatch,hostname,addresses):
    import maas_bootstrap.worker as w
    monkeypatch.setattr(w.platform,'node',lambda:hostname)
    monkeypatch.setattr(w.platform,'system',lambda:'Linux')
    monkeypatch.setattr(w.os,'geteuid',lambda:0)
    def ip_only(args):
        assert args==['ip','-j','-4','address','show']
        return json.dumps([{'addr_info':[{'local':address} for address in addresses]}])
    monkeypatch.setattr(w,'run',ip_only)
    guard_clone_host()


def test_controller_address_still_blocks_reused_name_and_secondary_address(monkeypatch):
    import maas_bootstrap.worker as w
    monkeypatch.setattr(w.platform,'node',lambda:'XZ-31-002')
    monkeypatch.setattr(w.platform,'system',lambda:'Linux')
    monkeypatch.setattr(w.os,'geteuid',lambda:0)
    monkeypatch.setattr(w,'run',lambda args:json.dumps([{'addr_info':[{'local':'10.11.31.2'},{'local':'10.11.31.1'}]}]))
    with pytest.raises(BootstrapError,match='PROTECTED_HOST_WORKER_FORBIDDEN'):guard_clone_host()
