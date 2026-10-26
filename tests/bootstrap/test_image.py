import json,importlib.util
from pathlib import Path
import pytest
from test_client import setup,CODE
from maas_bootstrap.client import BootstrapError,atomic_json

REPO=Path(__file__).resolve().parents[2]
spec=importlib.util.spec_from_file_location('prepare_worker_image',REPO/'scripts/maas/prepare_worker_image.py')
image=importlib.util.module_from_spec(spec);spec.loader.exec_module(image)


def inputs(setup,tmp_path):
    settings,sign=setup;d=dict(settings.data)
    cert=Path(d['tls_certificate_file'])
    cert.write_bytes((REPO/'results/maas-e5-preparation/bootstrap-server.crt').read_bytes())
    d.update(state_dir='/var/lib/maas-bootstrap',worker_data_dir='/var/lib/maas-bootstrap/gpustack-worker')
    p=tmp_path/'image-settings.json';atomic_json(p,d)
    root=tmp_path/'offline';(root/'etc').mkdir(parents=True)
    (root/'etc/os-release').write_text('ID=ubuntu\nVERSION_ID="24.04"\n')
    return root,p


def test_offline_image_contains_only_template_configuration(setup,tmp_path):
    root,cfg=inputs(setup,tmp_path);r=image.build(root,cfg)
    assert r['worker_started'] is False and r['state_included'] is False
    assert not (root/'var/lib/maas-bootstrap').exists()
    assert CODE not in json.dumps(r)
    assert (root/'etc/maas/shared-code').stat().st_mode&0o777==0o600
    assert (root/'etc/maas').stat().st_mode&0o777==0o700
    assert (root/'etc/systemd/system/multi-user.target.wants/maas-worker-bootstrap.service').is_symlink()
    assert not (root/'opt/maas/lib/maas_bootstrap/service.py').exists()
    assert (root/'opt/maas/lib/maas_bootstrap/storage.py').is_file()
    written=json.loads((root/'etc/maas/bootstrap.json').read_text())
    assert written['model_root'] and written['model_cache_dir'] and written['model_root']!=written['worker_data_dir']
    wrapper=(root/'opt/maas/bin/maas-worker-bootstrap').read_text()
    assert 'exec /usr/bin/python3' in wrapper and 'curl' not in wrapper
    with pytest.raises(BootstrapError,match='IMAGE_DESTINATION_EXISTS'):image.build(root,cfg)


@pytest.mark.parametrize('name', ['etc/machine-id','etc/ssh/ssh_host_ed25519_key','var/lib/gpustack/worker_token','var/lib/docker/containers/old/config.v2.json'])
def test_rejects_dirty_identity_without_deleting_it(setup,tmp_path,name):
    root,cfg=inputs(setup,tmp_path);p=root/name;p.parent.mkdir(parents=True,exist_ok=True);p.write_text('preserve-me')
    with pytest.raises(BootstrapError,match='IMAGE_GENERALIZATION_REQUIRED'):image.build(root,cfg)
    assert p.read_text()=='preserve-me' and not (root/'etc/maas').exists()


def test_symlink_escape_cannot_write_outside_image(setup,tmp_path):
    root,cfg=inputs(setup,tmp_path);outside=tmp_path/'outside';outside.mkdir()
    (root/'opt').symlink_to(outside)
    with pytest.raises(BootstrapError,match='IMAGE_SYMLINK_PATH'):image.build(root,cfg)
    assert list(outside.iterdir())==[] and not (root/'etc/maas').exists()


def test_ubuntu_os_release_link_inside_image_is_supported(setup,tmp_path):
    root,cfg=inputs(setup,tmp_path);p=root/'etc/os-release';p.unlink()
    q=root/'usr/lib';q.mkdir(parents=True);(q/'os-release').write_text('ID=ubuntu\n')
    p.symlink_to('../usr/lib/os-release')
    assert image.build(root,cfg)['worker_started'] is False
