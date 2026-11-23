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
    assert (root/'etc/systemd/system/timers.target.wants/maas-worker-bootstrap.timer').is_symlink()
    assert not (root/'etc/systemd/system/multi-user.target.wants/maas-worker-bootstrap.service').exists()
    assert (root/'opt/maas/lib/maas_bootstrap/startup.py').is_file()
    assert (root/'opt/maas/lib/maas_bootstrap/shutdown_events.py').is_file()
    assert not (root/'etc/systemd/system/multi-user.target.wants/maas-worker-shutdown-notify.service').exists()
    assert not (root/'opt/maas/lib/maas_bootstrap/service.py').exists()
    assert (root/'opt/maas/lib/maas_bootstrap/storage.py').is_file()
    assert (root/'opt/maas/lib/maas_bootstrap/execution.py').is_file()
    assert (root/'opt/maas/lib/maas_bootstrap/worker_limits.py').is_file()
    written=json.loads((root/'etc/maas/bootstrap.json').read_text())
    assert written['model_root'] and written['model_cache_dir'] and written['model_root']!=written['worker_data_dir']
    wrapper=(root/'opt/maas/bin/maas-worker-bootstrap').read_text()
    assert 'exec /usr/bin/python3' in wrapper and 'curl' not in wrapper
    with pytest.raises(BootstrapError,match='IMAGE_DESTINATION_EXISTS'):image.build(root,cfg)


@pytest.mark.parametrize('release_mode', [False, True])
def test_offline_image_preserves_nfs_policy_without_mounting_or_adding_instance_state(setup, tmp_path, release_mode):
    from test_nfs import CONFIG
    root, cfg = inputs(setup, tmp_path)
    data = json.loads(cfg.read_text())
    policy = {'model_nfs_from_release': True} if release_mode else {'model_nfs': CONFIG}
    data.update(policy)
    atomic_json(cfg, data)
    result = image.build(root, cfg)
    written = json.loads((root/'etc/maas/bootstrap.json').read_text())
    assert {k: v for k, v in written.items() if k.startswith('model_nfs')} == policy
    assert (root/'opt/maas/lib/maas_bootstrap/nfs.py').is_file()
    assert any('nfs-common' in text for text in result['required_before_sealing'])
    assert not result['state_included'] and not result['worker_started']


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


@pytest.mark.parametrize('automatic', [False, True])
def test_diskless_template_copies_only_grant_path_and_rejects_dirty_runtime(setup,tmp_path,automatic):
    root,cfg=inputs(setup,tmp_path)
    data=json.loads(cfg.read_text())
    del data['cluster_id']
    data.update(state_dir='/run/maas-bootstrap',worker_data_dir='/run/maas-bootstrap/gpustack-worker',
                worker_start_grant_file='/run/maas-start/boot-grant')
    if automatic:data['worker_start_automatic']=True
    atomic_json(cfg,data)
    grant=root/'run/maas-start/boot-grant'
    grant.parent.mkdir(parents=True);grant.write_text('fixture')
    with pytest.raises(BootstrapError,match='IMAGE_GENERALIZATION_REQUIRED'):image.build(root,cfg)
    grant.unlink()
    image.build(root,cfg)
    assert not grant.exists() and not (root/'run/maas-bootstrap').exists()
    assert (root/'opt/maas/lib/maas_bootstrap/handoff.py').is_file()
    assert (root/'opt/maas/lib/maas_bootstrap/delivery.py').is_file()
    units=root/'etc/systemd/system'
    assert not (units/'multi-user.target.wants/maas-worker-bootstrap.service').is_symlink()
    timer=units/'timers.target.wants/maas-worker-bootstrap.timer'
    path=units/'multi-user.target.wants/maas-worker-bootstrap.path'
    assert timer.is_symlink()==automatic and path.is_symlink()!=automatic
    active=timer if automatic else path
    assert active.readlink()==Path('/etc/systemd/system/'+active.name)
    assert 'PathExists=/run/maas-start/boot-grant\n' in (units/'maas-worker-bootstrap.path').read_text()
    assert 'Unit=maas-worker-bootstrap.timer\n' in (units/'maas-worker-bootstrap.path').read_text()
    installed=json.loads((root/'etc/maas/bootstrap.json').read_text())
    assert installed['cluster_id']==1
    assert installed.get('worker_start_automatic',False)==automatic
    assert installed['worker_start_grant_file']==data['worker_start_grant_file']


def test_notification_image_has_public_ca_and_enabled_unit_but_no_node_credential(setup,tmp_path):
    root,cfg=inputs(setup,tmp_path)
    data=json.loads(cfg.read_text())
    data.update(state_dir='/run/maas-bootstrap',worker_data_dir='/run/maas-bootstrap/gpustack-worker',
                worker_start_grant_file='/run/maas-start/boot-grant',worker_start_automatic=True)
    data['shutdown_events']={'url':'https://maas.example.invalid/maas/internal/worker-shutdown-events',
                             'tls_certificate_file':data['tls_certificate_file']}
    atomic_json(cfg,data)
    image.build(root,cfg)
    installed=json.loads((root/'etc/maas/bootstrap.json').read_text())
    assert installed['shutdown_events']['tls_certificate_file']=='/etc/maas/shutdown-events-ca.crt'
    assert (root/'etc/maas/shutdown-events-ca.crt').stat().st_mode&0o777==0o644
    assert (root/'etc/systemd/system/multi-user.target.wants/maas-worker-shutdown-notify.service').is_symlink()
    assert not (root/'run/maas-bootstrap').exists()
    assert not list(root.rglob('shutdown-event.json'))
    assert 'shutdown_event_credential' not in (root/'etc/maas/bootstrap.json').read_text()


def test_notification_cannot_be_enabled_for_legacy_random_identity_image(setup,tmp_path):
    root,cfg=inputs(setup,tmp_path)
    data=json.loads(cfg.read_text())
    data['shutdown_events']={'url':'https://maas.example.invalid/maas/internal/worker-shutdown-events',
                             'tls_certificate_file':data['tls_certificate_file']}
    atomic_json(cfg,data)
    with pytest.raises(BootstrapError,match='GPU_IDENTITY_REQUIRED'):
        image.build(root,cfg)
    assert not (root/'etc/maas').exists()
