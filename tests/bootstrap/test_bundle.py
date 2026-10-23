import hashlib,importlib.util,json,subprocess,tarfile
from pathlib import Path
import pytest

REPO=Path(__file__).resolve().parents[2]
def module(name):
    spec=importlib.util.spec_from_file_location(name,REPO/'scripts/maas'/f'{name}.py')
    m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m);return m


def test_bundle_is_reproducible_traceable_and_detects_tampering(tmp_path,monkeypatch):
    b=module('build_worker_bundle');v=module('verify_worker_bundle')
    repo=tmp_path/'repo';repo.mkdir();(repo/'client.py').write_text('# public source\n')
    for args in (['init','-q'],['add','client.py'],['-c','user.name=Test','-c','user.email=test@example.invalid','commit','-qm','fixture']):
        subprocess.run(['git',*args],cwd=repo,check=True,capture_output=True)
    monkeypatch.setattr(b,'REPO',repo);monkeypatch.setattr(b,'SOURCES',['client.py'])
    first=tmp_path/'first.tgz';second=tmp_path/'second.tgz'
    a=b.build(first);c=b.build(second)
    assert a['sha256']==c['sha256']==hashlib.sha256(first.read_bytes()).hexdigest()
    target=tmp_path/'extracted';target.mkdir()
    with tarfile.open(first) as t:
        # Produced from explicit in-test public names; no untrusted archive extraction.
        for entry in t.getmembers():
            assert entry.name in ('client.py','bundle-manifest.json') and entry.isfile()
            (target/entry.name).write_bytes(t.extractfile(entry).read())
    assert v.verify(target)['ok'] is True
    (target/'client.py').write_text('modified')
    with pytest.raises(ValueError,match='Hash mismatch'):v.verify(target)
    (repo/'client.py').write_text('dirty source')
    with pytest.raises(ValueError,match='Commit source'):b.build(tmp_path/'dirty.tgz')
    assert not (tmp_path/'dirty.tgz').exists()


def test_bundle_manifest_rejects_path_escape_and_extra_members(tmp_path):
    v=module('verify_worker_bundle');p=tmp_path/'bundle-manifest.json'
    p.write_text(json.dumps({'schema_version':1,'contains_secrets':False,'files':{'../escape':{}},'source_commit':'test'}))
    with pytest.raises(ValueError,match='Invalid member path'):v.verify(tmp_path)
    p.write_text(json.dumps({'schema_version':1,'contains_secrets':False,'files':{},'source_commit':'test'}))
    (tmp_path/'unexpected-secret').write_text('dummy')
    with pytest.raises(ValueError,match='Unexpected'):v.verify(tmp_path)
