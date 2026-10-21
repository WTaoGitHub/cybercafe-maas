import json,os
import pytest
from maas_platform.operator_config import create_private,new_session,private_json,wait_collector


def test_fresh_sessions_fence_incomplete_windows(tmp_path):
    create_private(tmp_path/'binding.json',{'worker_id':2,'model_ids':[1,9]})
    path=new_session(tmp_path);config=private_json(path)
    assert path.stat().st_mode & 0o777==0o600
    assert config['worker_id']==2 and config['recovery_unit'].startswith('maas-live-recover-')
    with pytest.raises(RuntimeError):new_session(tmp_path)
    create_private(config['journal'],{'phase':'RECOVERED'})
    with pytest.raises(RuntimeError):new_session(tmp_path)
    os.unlink(config['journal']);create_private(config['journal'],{'phase':'RESUMED'})
    assert new_session(tmp_path)!=path


def test_binding_secrets_are_not_accepted_from_public_or_symlink_files(tmp_path):
    p=tmp_path/'binding.json';p.write_text('{}');p.chmod(0o644)
    with pytest.raises(ValueError):private_json(p)
    p.chmod(0o600);link=tmp_path/'link';link.symlink_to(p)
    with pytest.raises(ValueError):private_json(link)


def test_collector_waits_for_fresh_recovery_and_times_out_closed(tmp_path):
    p=tmp_path/'health.json';ticks=iter([0,0,1,2,3])
    def refresh(_):p.write_text(json.dumps({'ok':True,'updated_at':100,'observed_at':99}))
    wait_collector(p,3,clock=lambda:next(ticks),sleep=refresh,now=lambda:100)
    p.write_text(json.dumps({'ok':True,'updated_at':1,'observed_at':1}))
    ticks=iter([0,0,1,2])
    with pytest.raises(RuntimeError,match='paused'):wait_collector(p,2,clock=lambda:next(ticks),sleep=lambda _:None,now=lambda:100)
