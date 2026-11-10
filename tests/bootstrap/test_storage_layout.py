import copy,json
from pathlib import Path
import pytest
from test_client import setup,TOKEN,CODE,Fetcher
from maas_bootstrap.client import BootstrapError,LocalSettings,atomic_json
from maas_bootstrap import storage,worker


def image(monkeypatch):
    def read(args,**kw):
        assert args[:3]==['docker','image','inspect']
        return json.dumps([{'Config':{'Env':['GPUSTACK_RUNTIME_DEPLOY_MIRRORED_DEPLOYMENT_IGNORE_VOLUMES=/opt/vendor;/var/run/cdi']}}])
    monkeypatch.setattr(worker,'run',read)


def test_public_marker_and_cache_are_repeatable_and_do_not_include_identity(setup):
    settings,_=setup
    a=storage.prepare(settings);b=storage.prepare(settings)
    assert a==b and a['published_models_readonly'] and a['cache_writable']
    text=(Path(a['marker_path'])/'deployment.json').read_text()
    assert TOKEN not in text and CODE not in text
    assert not Path(settings.data['worker_data_dir']).exists()
    assert not Path(settings.data['state_dir']).exists()
    assert (Path(a['marker_path'])/'deployment.json').stat().st_mode&0o777==0o644


@pytest.mark.parametrize('kind',['private-child','private-parent','cache-child','broad-root','colon','traversal'])
def test_storage_paths_cannot_include_identity_or_overlap(setup,tmp_path,kind):
    settings,_=setup;d=copy.deepcopy(settings.data)
    d['model_root']={'private-child':d['worker_data_dir']+'/models','private-parent':str(tmp_path),
        'cache-child':d['model_cache_dir']+'/models','broad-root':'/root','colon':str(tmp_path/'models:rw'),
        'traversal':str(tmp_path)+'/../models'}[kind]
    p=tmp_path/'bad-settings.json';atomic_json(p,d)
    with pytest.raises(BootstrapError,match='LOCAL_CONFIG_INVALID'):LocalSettings(p)


def test_symlink_storage_root_and_marker_are_not_followed(setup,tmp_path):
    s,_=setup;outside=tmp_path/'outside';outside.mkdir()
    Path(s.data['model_root']).symlink_to(outside,target_is_directory=True)
    with pytest.raises(BootstrapError,match='MODEL_STORAGE_UNTRUSTED'):storage.prepare(s)
    assert not list(outside.iterdir())
    Path(s.data['model_root']).unlink();r=storage.prepare(s)
    marker=Path(r['marker_path'])/'deployment.json';marker.unlink();marker.symlink_to(tmp_path/'missing')
    with pytest.raises(BootstrapError,match='MODEL_STORAGE_UNTRUSTED'):storage.prepare(s)
    assert marker.is_symlink()


def test_writable_storage_root_and_conflicting_marker_are_rejected(setup):
    s,_=setup;r=storage.prepare(s);root=Path(r['model_root']);root.chmod(0o777)
    with pytest.raises(BootstrapError,match='MODEL_STORAGE_UNTRUSTED'):storage.status(s)
    root.chmod(0o755);marker=Path(r['marker_path'])/'deployment.json';marker.write_text('{}')
    with pytest.raises(BootstrapError,match='MODEL_MARKER_CONFLICT'):storage.prepare(s)
    assert marker.read_text()=='{}'


def test_native_layout_excludes_identity_preserves_vendor_and_separates_cache(setup,monkeypatch):
    s,sign=setup;image(monkeypatch);runtime=worker.NativeDockerWorker()
    spec=runtime.desired_layout(s,sign());env=spec['env']
    assert set(env['GPUSTACK_RUNTIME_DEPLOY_MIRRORED_DEPLOYMENT_IGNORE_VOLUMES'].split(';'))=={'/var/lib/gpustack','/opt/vendor','/var/run/cdi'}
    assert env['GPUSTACK_RUNTIME_DEPLOY_MIRRORED_NAME']==worker.NAME
    mounts={m['target']:m for m in spec['mounts']}
    assert mounts[s.data['model_root']]['rw'] is False
    assert mounts[s.data['model_cache_dir']]['rw'] is True
    assert env['GPUSTACK_CACHE_DIR']==s.data['model_cache_dir']
    assert mounts['/var/lib/gpustack']['source']==s.data['worker_data_dir']
    storage.prepare(s)
    assert runtime.validate_layout(s,sign(),{'layout':spec})['layout_version']==worker.LAYOUT_VERSION


@pytest.mark.parametrize('drift',['legacy','writable-models','identity-extra-mount','ignore-removed','label-forgery'])
def test_existing_layout_drift_requires_maintenance(setup,monkeypatch,drift):
    s,sign=setup;image(monkeypatch);runtime=worker.NativeDockerWorker()
    spec=runtime.desired_layout(s,sign());changed=copy.deepcopy(spec)
    if drift=='legacy':changed={}
    elif drift=='writable-models':next(m for m in changed['mounts'] if m['target']==s.data['model_root'])['rw']=True
    elif drift=='identity-extra-mount':changed['mounts'].append({'source':s.data['worker_data_dir'],'target':'/secrets','rw':True})
    elif drift=='ignore-removed':changed['env']['GPUSTACK_RUNTIME_DEPLOY_MIRRORED_DEPLOYMENT_IGNORE_VOLUMES']='/opt/vendor'
    elif drift=='label-forgery':changed['sha256']='0'*64
    with pytest.raises(BootstrapError,match='NEEDS_MAINTENANCE'):runtime.validate_layout(s,sign(),{'layout':changed})
    assert not Path(s.data['model_root']).exists()


def test_operator_backup_receipt_is_required_and_cannot_allow_running_or_foreign_worker(setup):
    s,sign=setup;data=s.data['worker_data_dir'];old={'Id':'a'*64,'State':{'Running':False},
        'HostConfig':{'RestartPolicy':{'Name':'no'}},'Config':{'Labels':{worker.LABEL:'worker-v1'},
        'Image':sign()['manifest']['worker_image_digest']},'Mounts':[{'Type':'bind','Source':data,'Destination':'/var/lib/gpustack'}]}
    assert not worker.approved_stopped_backup(old,s,sign())
    atomic_json(Path(s.data['state_dir'])/'maintenance-backups.json',{'schema_version':1,'container_ids':[old['Id']]})
    assert worker.approved_stopped_backup(old,s,sign())
    for altered in [{**old,'State':{'Running':True}},{**old,'Id':'b'*64},{**old,'Mounts':[]}]:
        assert not worker.approved_stopped_backup(altered,s,sign())


def test_diskless_receipt_allows_only_the_exact_stopped_legacy_worker(setup,tmp_path):
    s,sign=setup
    legacy_dir=str(tmp_path/'legacy-native')
    old={'Id':'a'*64,'State':{'Running':False},'HostConfig':{'RestartPolicy':{'Name':'no'}},
         'Config':{'Labels':{worker.LABEL:'worker-v1'},'Image':sign()['manifest']['worker_image_digest']},
         'Mounts':[{'Type':'bind','Source':legacy_dir,'Destination':'/var/lib/gpustack'}]}
    receipt=Path(s.data['state_dir'])/'maintenance-backups.json'
    atomic_json(receipt,{'schema_version':1,'container_ids':[old['Id']]})
    diskless=sign({**sign()['manifest'],'worker_name_policy':worker.DISKLESS_POLICY})
    assert not worker.approved_stopped_backup(old,s,diskless)
    atomic_json(receipt,{'schema_version':2,'container_id':old['Id'],'legacy_worker_data_dir':legacy_dir})
    assert worker.approved_stopped_backup(old,s,diskless)
    assert not worker.approved_stopped_backup(old,s,sign())
    for altered in [
        {**old,'State':{'Running':True}}, {**old,'Id':'b'*64}, {**old,'Mounts':[]},
        {**old,'HostConfig':{'RestartPolicy':{'Name':'unless-stopped'}}},
        {**old,'Config':{**old['Config'],'Labels':{worker.LABEL:'worker-v1',worker.LABEL+'.session-id':'active'}}},
    ]:
        assert not worker.approved_stopped_backup(altered,s,diskless)


def test_apply_rejects_old_layout_before_runtime_changes(setup,monkeypatch):
    s,sign=setup;r=worker.NativeDockerWorker();image(monkeypatch)
    monkeypatch.setattr(r,'guard',lambda:None)
    monkeypatch.setattr(r,'inspect',lambda:{'fingerprint':worker.ReleaseVerifier().verify(s,sign())['fingerprint'],'running':True,'layout':{}})
    monkeypatch.setattr(r,'check_environment',lambda *a:pytest.fail('Must not reach environment or create/start'))
    with pytest.raises(BootstrapError,match='NEEDS_MAINTENANCE'):worker.apply(s,fetcher=Fetcher(sign()),backend=r)
    assert not Path(s.data['model_root']).exists()
