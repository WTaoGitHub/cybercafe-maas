"""T0 validation only: disposable PostgreSQL and mock model, never real OCR."""
import threading
import time
import json

import pytest

from test_integration import env,headers,submit
from maas_bench.backend import DatabaseObserver,TaskBackend
from maas_bench.contracts import Parameters
from maas_bench.saturation import RunPolicy,SaturationRunner
from maas_platform.worker import Worker
from maas_platform.sync_ingress import SyncIngress
from test_multi_executor_integration import group


def observer_for(settings,prefix='e3-unit12345:'):
    return DatabaseObserver(settings.database_url,'a','mock.echo','1',prefix,statement_ms=1000)


def test_observer_is_readonly_persistent_and_scoped_to_due_valid_work(env):
    client,s,settings,*_=env
    obs=observer_for(settings)
    try:
        obs.fresh()
        with pytest.raises(Exception):obs.connection.execute("UPDATE maintenance SET paused=true")
        for i in range(4):assert submit(client,'e3-unit12345:'+str(i),max_attempts=1).status_code==202
        assert submit(client,'other-run').status_code==202
        with s.db.transaction() as c:
            c.execute("UPDATE tasks SET next_run=now()+interval '1 minute' WHERE idempotency_key='e3-unit12345:1'")
            c.execute("UPDATE tasks SET deadline=now()-interval '1 second' WHERE idempotency_key='e3-unit12345:2'")
            c.execute("UPDATE tasks SET revision='unexpected' WHERE idempotency_key='e3-unit12345:3'")
        with pytest.raises(RuntimeError,match='drift'):obs.snapshot()
        with s.db.transaction() as c:c.execute("UPDATE tasks SET revision='1' WHERE revision='unexpected'")
        row=obs.snapshot(True)
        assert row['ready']==2 and row['global_queued']==5 and row['success_ids']==[]
        assert not obs.connection.closed
        with pytest.raises(RuntimeError,match='already'):obs.fresh()
    finally:obs.close()


def test_continuous_http_supply_real_database_commits_and_postwindow_results(env):
    client,s,settings,*_=env
    settings.max_running=settings.max_project_running=4
    client.headers.update(headers())
    obs=observer_for(settings,'e3-saturated12345:')
    backend=TaskBackend(client,obs,[{'input':{'text':'hello'},'parameters':{'delay_seconds':.18}}])
    halt=threading.Event();workers=[];errors=[]
    def work():
        try:
            worker=Worker(s,stop_claiming=halt)
            while not halt.is_set():
                if not worker.run_once():halt.wait(.003)
        except Exception as exc:errors.append(type(exc).__name__)
    try:
        backend.prepare()
        for _ in range(4):
            thread=threading.Thread(target=work);thread.start();workers.append(thread)
        initial=obs.snapshot()
        runner=SaturationRunner(backend,Parameters(4,4,4,4,1000,100),RunPolicy(
            seconds=1,min_successes=8,max_seconds=5,sample_seconds=.03,max_sample_gap=.5,
            prefill_seconds=5,drain_seconds=10),initial['generation'])
        result=runner.run()
        assert result['state']=='COMPLETE',result
        assert result['supply']['ready_queue_min']>0
        assert result['result_verification']['downloaded_results']==len(result['accepted_task_ids'])
        assert result['tail_completions']>0 and result['measurement_seconds']>=1
        assert not errors
    finally:
        halt.set()
        for worker in workers:worker.join(10);assert not worker.is_alive()
        obs.close()


@pytest.mark.parametrize('limit',[6,8,12])
def test_higher_limits_are_atomic_across_independent_processes(env,limit):
    client,s,settings,*_=env
    settings.max_running=settings.max_project_running=limit
    for i in range(limit+4):assert submit(client,'claim'+str(i)).status_code==202
    with group(settings,['claim']*(limit+3)) as (_,q):
        rows=[q.get(timeout=15)['result'] for _ in range(limit+3)]
    accepted=[r for r in rows if r]
    assert len(accepted)==limit and len({r['id'] for r in accepted})==limit


@pytest.mark.parametrize('limit',[6,8,12])
def test_sync_and_async_processes_share_global_and_per_project_limits(env,limit):
    client,s,settings,auth,_=env
    settings.max_running=limit;settings.max_project_running=limit//2
    token=SyncIngress(s).begin(auth.authenticate('gpustack_registered_a'),'mock.echo',30)
    for i in range(limit):
        assert submit(client,'a'+str(i)).status_code==202
        assert submit(client,'b'+str(i),key='b').status_code==202
    with group(settings,['claim']*(limit+3)) as (_,q):
        rows=[q.get(timeout=15)['result'] for _ in range(limit+3)]
    accepted=[r for r in rows if r]
    assert len(accepted)==limit-1
    assert sum(r['project']=='a' for r in accepted)+1==limit//2
    assert sum(r['project']=='b' for r in accepted)==limit//2
    SyncIngress(s).finish(token,True)


def test_same_project_two_keys_cannot_multiply_atomic_queue_allowance(env):
    client,s,settings,*_=env
    settings.max_queued=settings.max_project_queued=4
    keyfile=settings.bindings_file.parent/'key-c';keyfile.write_text('gpustack_registered_c');keyfile.chmod(0o600)
    data=json.loads(settings.bindings_file.read_text())
    data['credentials'].append({'id':'c','project':'a','secret_file':str(keyfile),'models':['mock.echo']})
    settings.bindings_file.write_text(json.dumps(data))
    actions=['admit:'+('a' if i%2 else 'c')+':submit'+str(i) for i in range(12)]
    with group(settings,actions) as (_,q):rows=[q.get(timeout=15)['result'] for _ in actions]
    assert sum(r.get('created',False) for r in rows)==4
    assert sum(r.get('error_code')=='QUEUE_FULL' for r in rows)==8
    with s.db.transaction() as c:
        assert c.execute('SELECT count(*) n FROM tasks').fetchone()['n']==4


def test_ready_inputs_remain_pinned_after_ttl_but_deleted_inputs_are_not_supply(env):
    client,s,settings,*_=env
    art=client.post('/maas/v1/artifacts',headers=headers(),content=b'pinned-input').json()['id']
    assert submit(client,'e3-unit12345:pinned',max_attempts=1,input={'artifact_id':art}).status_code==202
    obs=observer_for(settings)
    try:
        with s.db.transaction() as c:c.execute("UPDATE artifacts SET expires_at=now()-interval '1 second' WHERE id=%s",(art,))
        assert obs.snapshot()['ready']==1
        with s.db.transaction() as c:c.execute('UPDATE artifacts SET deleted_at=now() WHERE id=%s',(art,))
        assert obs.snapshot()['ready']==0
    finally:obs.close()


def test_boundaries_count_committed_ids_not_updated_at_timestamps(env):
    client,s,settings,*_=env
    task=submit(client,'e3-unit12345:commit-race',max_attempts=1).json()['id']
    obs=observer_for(settings)
    import psycopg
    connection=psycopg.connect(settings.database_url)
    try:
        connection.execute("UPDATE tasks SET state='SUCCEEDED',attempts=1,updated_at=now()-interval '1 hour' WHERE id=%s",(task,))
        connection.execute("INSERT INTO task_usage_events(task_id,project,quantity) VALUES(%s,'a',1)",(task,))
        assert obs.snapshot(True)['success_ids']==[]
        connection.commit()
        assert obs.snapshot(True)['success_ids']==[task]
    finally:connection.close();obs.close()
