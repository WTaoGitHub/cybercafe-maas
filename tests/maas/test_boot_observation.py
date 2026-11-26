"""MAAS_TEST_DATABASE_URL must point to a disposable *_test PostgreSQL database."""
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import subprocess
import sys
from uuid import uuid4

import pytest

from maas_bootstrap import runtime_witness
from maas_platform import boot_observation as observer
from maas_platform.runtime_proof import signature
from test_runtime_recovery import IDENTITY, create_attempt, enable, env, guarded, protected, request


@pytest.mark.parametrize('protected',[12],indirect=True)
def test_signed_handoff_observes_early_and_late_unknown_after_restart(protected,tmp_path,monkeypatch):
    client, service, owner, _ = enable(protected,tmp_path)
    db,settings = service.db,service.settings
    gpu = owner.identity['gpu_uuid']
    settings.boot_observe_cluster = 1
    settings.runtime_boot_keys_dir = tmp_path/'test_agent_boot_keys'
    settings.runtime_boot_keys_dir.mkdir(mode=0o700)
    old, new = IDENTITY['boot'], str(uuid4())
    for boot,key in [(old,'3'*64),(new,'4'*64)]:
        p=settings.runtime_boot_keys_dir/f'{gpu}.{boot}.key'
        p.write_text(key);p.chmod(0o600)
    def source(seq,boot):
        return dict(seq=seq,boot_id=boot,worker_id=31,gpu_uuid=gpu,session_id=str(uuid4()))
    def scan(row):
        return observer.observe(db,1,lambda:[row])
    def witness(row,key):
        boot=row['boot_id']
        offer=request(client,gpu,key=key,boot_id=boot).json()
        check=offer['boot_check']
        assert check['session_id']==row['session_id'] and check['boot_id']==boot
        def submit(mac):
            return request(client,gpu,'boot_observation',key=key,boot_id=boot,nonce=check['nonce'],signature=mac)
        assert submit('0'*64).status_code==409
        # Exercise the real host responder without a model container.
        kernel=tmp_path/'test_agent_kernel_boot';kernel.write_text(boot)
        with monkeypatch.context() as patch:
            patch.setattr(runtime_witness,'BOOT',kernel)
            def exchange(_,origin,secret,payload):
                if payload['operation']=='runtime_poll':
                    return dict(offer,checks=[])
                answer=submit(payload['signature'])
                assert answer.status_code==204
            patch.setattr(runtime_witness,'exchange',exchange)
            runtime_witness.answer(None,None,key,gpu,{},boot=boot)
        return lambda:submit(signature(key,'boot-observe',check)).status_code
    baseline=source(1,old)
    assert scan(baseline)['confirmations']==0
    repeat=witness(baseline,'3'*64)
    with ThreadPoolExecutor(4) as pool:
        assert list(pool.map(lambda _:repeat(),range(4)))==[204]*4
        assert all(x['confirmations']==1 for x in pool.map(lambda _:scan(baseline),range(4)))
    # A new registration on the same boot has its own proof, but no new boot generation.
    same=source(2,old);scan(same);witness(same,'3'*64);scan(same)
    payload={**owner.identity,'identity':IDENTITY}
    owner.runtime_binding={'identity':IDENTITY,'signature':signature('3'*64,'bind',payload)}
    trials=[]
    for kind in ['sync','task']:
        token,disconnect=create_attempt(protected,kind,idem='test_agent_boot_order')
        owner.report(kind+'.'+token,finished=False)
        trials.append((token,disconnect))
    trials[0][1]()
    replacement=source(3,new)
    assert scan(replacement)['recorded_candidates']==0
    assert repeat()==409
    witness(replacement,'4'*64)
    # Kill the registration transaction after its history insert. A new controller
    # can complete it from the already committed challenge response.
    crash = """import json,os,sys
from maas_platform.db import Database
from maas_platform.boot_observation import observe_source
with Database(os.environ['MAAS_TEST_DATABASE_URL']).transaction() as c:
    observe_source(c,1,json.load(sys.stdin))
    os._exit(73)
"""
    assert subprocess.run([sys.executable,'-B','-c',crash],input=json.dumps(replacement),
                          text=True,timeout=8).returncode==73
    with db.transaction() as c:
        assert c.execute('SELECT count(*) n FROM boot_session_history').fetchone()['n']==2
    with ThreadPoolExecutor(3) as pool:
        assert all(r['recorded_candidates']==1 for r in pool.map(lambda _:scan(replacement),range(3)))
    with db.transaction() as c:
        assert observer.grant_session(c,1,gpu,old) is None
        assert str(observer.grant_session(c,1,gpu,new))==replacement['session_id']
        assert observer.grant_session(c,1,protected[2][1].identity['gpu_uuid'],new) is None
    trials[1][1]()  # Async timeout arrives after the signed handoff.
    assert scan(replacement)['pending_candidates']==2
    assert scan(baseline)['confirmations']==3
    reused=source(4,old)
    assert scan(reused)['confirmations']==3
    assert 'boot_check' not in request(client,gpu,key='3'*64,boot_id=old).json()
    with db.transaction() as c:
        assert [r['generation'] for r in c.execute('SELECT generation FROM boot_session_history ORDER BY seq')]==[1,1,2]
        assert {r['token'] for r in c.execute('SELECT token FROM boot_unknown_candidates')}=={t for t,_ in trials}
        for table in ['sync_activity','backend_activity']:
            assert c.execute(f'SELECT state FROM {table}').fetchone()['state']=='UNKNOWN'
        assert c.execute('SELECT state,attempts FROM tasks').fetchone()=={'state':'FAILED','attempts':1}
        assert not c.execute('SELECT 1 FROM task_usage_events UNION ALL SELECT 1 FROM sync_usage_events').fetchone()
        assert c.execute('SELECT count(*) n FROM inference_slots').fetchone()['n']==2


def test_native_journal_repeats_conflicts_and_uncommitted_handoffs(env):
    _,service,*_=env
    db=service.db
    sql=(Path(__file__).resolve().parents[2]/'scripts/maas/runtime/boot_observe_native.sql').read_text()
    with db.transaction() as c:
        c.execute('CREATE TABLE workers(id int,cluster_id int,external_id text,name text,deleted_at timestamptz)')
        c.execute('CREATE TABLE maas_worker_handoffs(worker_id int,session_id text,phase text)')
        c.execute("INSERT INTO workers VALUES(1,1,'test_agent_gpu','test_agent_gpu',NULL)")
        c.execute("INSERT INTO maas_worker_handoffs VALUES(1,%s,'registering')",(str(uuid4()),))
    with db.transaction() as c:
        c.execute(sql)
    with pytest.raises(RuntimeError):
        with db.transaction() as c:
            c.execute("UPDATE maas_worker_handoffs SET phase='active'")
            raise RuntimeError('test_agent_registration_aborted')
    with db.transaction() as c:
        assert not c.execute('SELECT * FROM maas_boot_registrations').fetchone()
        c.execute("UPDATE maas_worker_handoffs SET phase='active'")
        assert len(c.execute('SELECT * FROM maas_current_boot_registrations').fetchall())==1
        c.execute("INSERT INTO workers VALUES(2,1,'test_agent_gpu','test_agent_gpu',NULL)")
        assert not c.execute('SELECT * FROM maas_current_boot_registrations').fetchone()


def test_legacy_signer_cannot_attribute_a_grant_or_answer_a_boot_challenge(protected,tmp_path):
    client,service,owner,_=enable(protected,tmp_path)
    settings=service.settings
    settings.boot_observe_cluster=1
    token,close=create_attempt(protected,'sync')
    owner.report('sync.'+token,finished=False)
    close()
    gpu=owner.identity['gpu_uuid']
    with service.db.transaction() as c:
        row=c.execute('SELECT signer_boot FROM execution_runtime').fetchone()
        assert row['signer_boot'] is None
        assert c.execute('SELECT boot_observation_session FROM sync_activity').fetchone()['boot_observation_session'] is None
    # Unscoped legacy signatures cannot answer a boot challenge.
    forged=request(client,gpu,'boot_observation',boot_id=IDENTITY['boot'],nonce='e'*64,signature='e'*64)
    assert forged.status_code==403
    assert observer.observe(service.db,1,lambda:[])['pending_candidates']==0
    assert service.metrics()['unknown_sync_activity']==1
