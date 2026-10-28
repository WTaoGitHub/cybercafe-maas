"""Real PostgreSQL + filesystem tests across spawned OS processes, not threads.
Only uses env's disposable *_test database and mock inference.
"""
import multiprocessing as mp
import os,signal,threading,time
from contextlib import contextmanager
import httpx,pytest
from test_integration import env,submit,headers
from maas_platform.auth import AuthConnector
from maas_platform.service import TaskService
from maas_platform.worker import Worker
from maas_platform.sync_ingress import SyncIngress


def child(settings,action,start,out):
    auth=AuthConnector(settings,httpx.Client(transport=httpx.MockTransport(lambda _:httpx.Response(200,json={'data':[]}))))
    s=TaskService(settings,auth);out.put({'ready':os.getpid()});assert start.wait(15)
    if action=='claim':
        row=s.claim();out.put({'result':{'id':row['id'],'project':row['project']} if row else None})
    elif action=='recover':out.put({'result':s.recover()})
    elif action=='work':
        w=Worker(s);n=0
        while w.run_once():n+=1
        out.put({'result':n})
    elif action=='cleanup':
        for _ in range(20):s.cleanup();s.cleanup_orphans();time.sleep(.02)
        out.put({'result':True})
    elif action=='signal':
        from maas_platform.adapters import adapter_for
        stop=threading.Event();signal.signal(signal.SIGTERM,lambda *_:stop.set())
        adapter=adapter_for('mock');execute=adapter.execute
        def mark(ctx):out.put({'executing':ctx.task['id']});return execute(ctx)
        adapter.execute=mark
        w=Worker(s,stop_claiming=stop);assert w.run_once();assert stop.is_set()
        assert not w.run_once();out.put({'result':'graceful'})
    elif action=='intent-crash':
        row=s.claim();assert row;s.begin_backend(row);out.put({'intent':row['id']})
        time.sleep(30)
    else:raise AssertionError(action)


@contextmanager
def group(settings,actions):
    ctx=mp.get_context('spawn');start=ctx.Event();q=ctx.Queue()
    procs=[ctx.Process(target=child,args=(settings,a,start,q)) for a in actions]
    try:
        for p in procs:p.start()
        for _ in procs:assert 'ready' in q.get(timeout=15)
        start.set();yield procs,q
        for p in procs:p.join(timeout=20);assert p.exitcode==0
    finally:
        for p in procs:
            if p.is_alive():p.terminate();p.join(timeout=5)
        q.close()


def test_competing_processes_preserve_global_and_project_slots(env):
    client,s,settings,*_=env
    for i in range(4):
        assert submit(client,'a'+str(i)).status_code==202
        assert submit(client,'b'+str(i),key='b').status_code==202
    with group(settings,['claim']*4) as (_,q):rows=[q.get(timeout=15)['result'] for _ in range(4)]
    rows=[r for r in rows if r]
    assert len(rows)==2 and len({r['id'] for r in rows})==2 and {r['project'] for r in rows}=={'a','b'}


def test_sync_slot_is_shared_by_independent_async_processes(env):
    client,s,settings,auth,_=env
    for i in range(3):assert submit(client,str(i),key='b').status_code==202
    ingress=SyncIngress(s);token=ingress.begin(auth.authenticate('gpustack_registered_a'),'mock.echo',30)
    with group(settings,['claim']*3) as (_,q):rows=[q.get(timeout=15)['result'] for _ in range(3)]
    assert sum(r is not None for r in rows)==1
    ingress.finish(token,True)


def test_paused_admission_stops_all_processes_claiming(env):
    client,s,settings,*_=env;submit(client);s.pause(True)
    with group(settings,['claim']*3) as (_,q):assert [q.get(timeout=15)['result'] for _ in range(3)]==[None]*3


def test_two_processes_write_unique_results_while_gc_pins_active_inputs(env):
    client,s,settings,*_=env;settings.max_project_running=2
    art=client.post('/maas/v1/artifacts',headers={**headers(),'Content-Type':'text/plain'},content=b'shared input').json()
    ids=[submit(client,'item'+str(i),input={'text':'x','artifact_id':art['id']},parameters={'delay_seconds':.15}).json()['id'] for i in range(8)]
    with s.db.transaction() as c:c.execute("UPDATE artifacts SET expires_at=now()-interval '1 second' WHERE id=%s",(art['id'],))
    max_running=0
    with group(settings,['work','work','cleanup']) as (procs,q):
        while any(p.is_alive() for p in procs):
            with s.db.transaction() as c:max_running=max(max_running,c.execute("SELECT count(*) n FROM tasks WHERE state='RUNNING'").fetchone()['n'])
            time.sleep(.01)
        results=[q.get(timeout=5)['result'] for _ in procs]
    assert max_running==2
    with s.db.transaction() as c:
        rows=c.execute('SELECT * FROM tasks ORDER BY id').fetchall()
        assert c.execute('SELECT count(*) n FROM task_usage_events').fetchone()['n']==8
        assert c.execute('SELECT count(DISTINCT task_id) n FROM task_attempts').fetchone()['n']==8
    assert all(r['state']=='SUCCEEDED' and r['attempts']==1 for r in rows)
    for row in rows:
        value=client.get('/maas/v1/artifacts/'+row['result_artifact_id'],headers=headers()).json()
        assert value['outputs']['input_artifact']['bytes']==12


def test_sigterm_finishes_inflight_and_does_not_take_next_task(env):
    client,s,settings,*_=env
    first=submit(client,'first',parameters={'delay_seconds':.6}).json()['id'];second=submit(client,'second').json()['id']
    with group(settings,['signal']) as (procs,q):
        assert q.get(timeout=15)['executing']==first
        os.kill(procs[0].pid,signal.SIGTERM)
        assert q.get(timeout=15)['result']=='graceful'
    assert client.get('/maas/v1/tasks/'+first,headers=headers()).json()['state']=='SUCCEEDED'
    assert client.get('/maas/v1/tasks/'+second,headers=headers()).json()['state']=='QUEUED'


def test_crashed_dispatch_intent_is_quarantined_by_competing_recovery(env):
    client,s,settings,*_=env;ident=submit(client,'crash').json()['id']
    ctx=mp.get_context('spawn');start=ctx.Event();q=ctx.Queue();p=ctx.Process(target=child,args=(settings,'intent-crash',start,q));p.start()
    try:
        assert 'ready' in q.get(timeout=15);start.set();assert q.get(timeout=15)['intent']==ident
        p.kill();p.join(timeout=5)
    finally:
        if p.is_alive():p.kill();p.join(timeout=5)
        q.close()
    with s.db.transaction() as c:c.execute("UPDATE tasks SET lease_until=now()-interval '1 second' WHERE id=%s",(ident,))
    with group(settings,['recover']*3) as (_,q):assert sum(q.get(timeout=15)['result'] for _ in range(3))==1
    submit(client,'must-wait')
    with group(settings,['claim']*2) as (_,q):assert all(q.get(timeout=15)['result'] is None for _ in range(2))
    with s.db.transaction() as c:
        assert c.execute("SELECT state FROM backend_activity").fetchone()['state']=='UNKNOWN'
        assert c.execute('SELECT count(*) n FROM task_usage_events').fetchone()['n']==0


def test_shutdown_before_dispatch_does_not_spend_execution_budget(env):
    client,s,settings,*_=env
    ident=submit(client,'shutdown-race',max_attempts=1).json()['id'];task=s.claim()
    assert s.defer_dispatch(task,reason='EXECUTOR_DRAINING')
    with group(settings,['work']) as (_,q):assert q.get(timeout=15)['result']==1
    row=client.get('/maas/v1/tasks/'+ident,headers=headers()).json()
    assert row['state']=='SUCCEEDED' and row['attempts']==2
    with s.db.transaction() as c:
        assert c.execute("SELECT error_code FROM task_attempts WHERE state='DEFERRED'").fetchone()['error_code']=='EXECUTOR_DRAINING'
        assert c.execute('SELECT count(*) n FROM task_usage_events').fetchone()['n']==1
