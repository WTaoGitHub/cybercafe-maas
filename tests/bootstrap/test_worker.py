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
    for host in ('XZ-31-001','XZ-31-002','tower-mNST8iPdv'):
        monkeypatch.setattr(w.platform,'node',lambda:host)
        with pytest.raises(BootstrapError,match='PROTECTED_HOST_WORKER_FORBIDDEN'):guard_clone_host()

def test_same_manifest_different_local_clones_have_distinct_names(setup,tmp_path):
    from maas_bootstrap.client import LocalSettings
    a,sign=setup;r1=FakeWorker();first=apply(a,fetcher=Fetcher(sign()),backend=r1)
    d={**a.data,'state_dir':str(tmp_path/'another-state')};p=tmp_path/'another.json';atomic_json(p,d)
    r2=FakeWorker();second=apply(LocalSettings(p),fetcher=Fetcher(sign()),backend=r2)
    assert first['worker_name']!=second['worker_name']
