from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
import threading
import pytest
from maas_platform.e1.control import E1Control
from maas_platform.e1.topology import Replica
from maas_platform.errors import ServiceError

IMAGE='sha256:'+'a'*64


@pytest.fixture
def env(tmp_path):
    now=[100.]
    policy={'m1':{'revision':'r1','image_id':IMAGE,'workers':['001','002']},
            'm2':{'revision':'r1','image_id':IMAGE,'workers':['001']}}
    c=E1Control.create(tmp_path/'e1.json',policy,{'001':2,'002':1},
                       global_slots=3,project_slots=2,recovery_hold_seconds=0,clock=lambda:now[0])
    replicas=[Replica('m1','r1','001',1,'m1-001',IMAGE,1,100.,'a'),
              Replica('m1','r1','002',2,'m1-002',IMAGE,1,100.,'b'),
              Replica('m2','r1','001',3,'m2-001',IMAGE,1,100.,'c')]
    c.publish(replicas)
    return c,now,replicas


def reserve(c,token='t',model='m1',project='p',kind='async'):
    return c.reserve(token,project,model,'r1',kind,c.clock()+20)


def evidence(c,session,**kw):
    return dict(worker='002',session=session,observed_at=c.clock(),**kw)


def boundary(c,session):
    return evidence(c,session,bypass_closed=True,scheduling_blocked=True,route_withdrawn=True)


def probe(c):
    return lambda r,s:evidence(c,s,ok=True,**{k:r[k] for k in ('route','revision','image_id','instance_id')})


def test_maintenance_drains_only_target_healthy_models_continue(env):
    c,_,_=env
    reserve(c,'on001');target=reserve(c,'on002',kind='sync');assert target['worker']=='002'
    session=c.begin('002','planned')
    assert not c.drained('002',session)
    assert c.capacity('m1')['workers']==['001'] and c.capacity('m2')['workers']==['001']
    c.finish('on001',True)
    assert reserve(c,'other','m2',project='other')['worker']=='001'
    with pytest.raises(ServiceError,match='Unresolved'):c.stop('002',session,boundary(c,session),lambda *a:None)
    c.finish('on002',True)
    assert c.drained('002',session)
    called=[]
    c.stop('002',session,boundary(c,session),lambda w,s:(called.append(w) or
        evidence(c,s,worker_stopped=True,instances_stopped=True,gpu_idle=True)))
    assert called==['002'] and c.snapshot()['workers']['001']['mode']=='ACTIVE'
    c.recover('002',session,lambda *a:None,probe(c))
    assert c.snapshot()['workers']['002']['mode']=='ACTIVE'


def test_unknown_is_worker_scoped_and_not_replayed(env):
    c,_,_=env
    reserve(c,'a');reserve(c,'b');c.finish('a',True);c.finish('b',False)
    assert c.capacity('m1')['workers']==['001']
    assert reserve(c,'m2','m2')['worker']=='001'
    with pytest.raises(ServiceError,match='never resend'):reserve(c,'b')
    session=c.begin('002','investigate')
    assert not c.drained('002',session)
    c.resolve('b','GPU process termination verified externally')
    assert c.drained('002',session)


def test_expiry_survives_rejection_and_process_restart(env):
    c,now,_=env;reserve(c);now[0]+=21
    restarted=E1Control(c.path,clock=c.clock)
    with pytest.raises(ServiceError):reserve(restarted,'t')
    assert restarted.snapshot()['requests']['t']['state']=='UNKNOWN'
    assert restarted.capacity('m1')['workers']==['002']


def test_legacy_unknown_keeps_global_fence(env):
    c,*_=env;c.ambiguous('legacy','Generic gateway may reach both workers')
    assert c.capacity('m1')['healthy_slots']==0 and c.capacity('m2')['healthy_slots']==0
    with pytest.raises(ServiceError):reserve(c)
    c.resolve('legacy','All possible upstream executions confirmed terminated')
    assert c.capacity('m2')['healthy_slots']==1


def test_two_models_one_worker_failure_is_scoped(env):
    c,*_=env;c.isolate('001','one instance unhealthy',model='m1')
    assert c.capacity('m1')['workers']==['002']
    assert c.capacity('m2')['workers']==['001']
    c.isolate('002','worker disconnected')
    assert c.capacity('m1')['healthy_slots']==0
    assert reserve(c,'healthy','m2')['worker']=='001'


def test_capacity_prevents_overlapping_last_replica_maintenance(env):
    c,*_=env
    with pytest.raises(ServiceError,match='Control host'):c.begin('001','no')
    c.isolate('001','offline')
    with pytest.raises(ServiceError,match='last healthy'):c.begin('002','no spare')
    assert c.snapshot()['workers']['002']['mode']=='ACTIVE'


def test_atomic_reservations_share_sync_async_limits(env):
    c,*_=env
    def attempt(i):
        try:return reserve(c,str(i),kind='sync' if i%2 else 'async')
        except ServiceError:return None
    with ThreadPoolExecutor(max_workers=12) as pool:rows=list(pool.map(attempt,range(20)))
    successes=[r for r in rows if r]
    assert len(successes)==2 and len({r['worker'] for r in successes})==2
    assert len(c.snapshot()['requests'])==2


@pytest.mark.parametrize('bad',['stale','wrong-session','bypass','scheduler','route','gpu','hook-error'])
def test_stop_requires_real_boundary_and_process_proof(env,bad):
    c,*_=env;s=c.begin('002','test');b=boundary(c,s);called=[]
    if bad=='stale':b['observed_at']=0
    if bad=='wrong-session':b['session']='old'
    if bad=='bypass':b['bypass_closed']=False
    if bad=='scheduler':b['scheduling_blocked']=False
    if bad=='route':b['route_withdrawn']=False
    def stop(w,session):
        called.append(w)
        if bad=='hook-error':raise OSError('node lost')
        return evidence(c,s,worker_stopped=True,instances_stopped=True,gpu_idle=bad!='gpu')
    with pytest.raises(Exception):c.stop('002',s,b,stop)
    state=c.snapshot()['workers']['002']['mode']
    assert state==('STOP_UNCONFIRMED' if bad in ('gpu','hook-error') else 'DRAINING')
    assert c.capacity('m1')['workers']==['001']
    assert called==(['002'] if bad in ('gpu','hook-error') else [])


def test_recovery_failure_quarantines_probe_without_blocking_healthy_requests(env):
    c,*_=env;c.isolate('002','fault');s=c.snapshot()['workers']['002']['session']
    def fail(r,session):
        # A second thread can dispatch while the probe network callback runs.
        with ThreadPoolExecutor(max_workers=1) as pool:
            assert pool.submit(reserve,c,'healthy','m2','other').result(timeout=2)['worker']=='001'
        raise TimeoutError('unconfirmed probe')
    with pytest.raises(TimeoutError):c.recover('002',s,lambda *a:None,fail)
    assert c.snapshot()['workers']['002']['mode']=='RECOVERING'
    assert any(r['state']=='UNKNOWN' and r['kind']=='probe' for r in c.snapshot()['requests'].values())
    assert c.capacity('m1')['workers']==['001']


def test_fresh_collection_does_not_automatically_rejoin_faulted_worker(env):
    c,_,rows=env;c.isolate('002','fault');c.publish(rows)
    assert c.capacity('m1')['workers']==['001']


def test_recovery_refuses_topology_change_mid_probe(env):
    c,_,rows=env;c.isolate('002','fault');s=c.snapshot()['workers']['002']['session']
    def changed(r,session):
        c.publish([replace(x,instance_id=99) if x.worker=='002' else x for x in rows])
        return probe(c)(r,session)
    with pytest.raises(ServiceError):c.recover('002',s,lambda *a:None,changed)
    assert c.snapshot()['workers']['002']['mode']=='RECOVERING'


def test_stale_subset_cannot_route(env):
    c,now,rows=env;now[0]=131
    assert c.capacity('m2')['healthy_slots']==0
    with pytest.raises(ValueError):c.publish(rows)


def test_full_journal_denies_instead_of_forgetting_attempts(env):
    c,*_=env
    with c.transaction() as s:s['max_records']=1
    reserve(c);c.finish('t',True)
    with pytest.raises(ServiceError):reserve(c,'new')
    assert 't' in c.snapshot()['requests']


def test_model_only_recovery_does_not_require_stopping_control_worker(env):
    c,_,_=env;c.isolate('001','m1 instance failed',model='m1')
    session=c.snapshot()['model_isolations']['m1@001']['session']
    def check(r,s):
        assert c.capacity('m2')['workers']==['001']
        return dict(worker='001',session=s,observed_at=c.clock(),ok=True,
                    **{k:r[k] for k in ('route','revision','image_id','instance_id')})
    c.recover_model('001','m1',session,check)
    assert c.capacity('m1')['workers']==['001','002']


def test_recovery_hold_prevents_flapping(env):
    c,now,rows=env
    with c.transaction() as s:s['recovery_hold_seconds']=10
    c.isolate('002','fault');session=c.snapshot()['workers']['002']['session']
    with pytest.raises(ServiceError,match='hold'):c.recover('002',session,lambda *a:None,probe(c))
    now[0]+=10;c.recover('002',session,lambda *a:c.publish([replace(r,observed_at=now[0]) for r in rows]),probe(c))
    assert c.snapshot()['workers']['002']['mode']=='ACTIVE'


def _process_reserve(path, token, event, queue):
    event.wait(5)
    c=E1Control(path,clock=lambda:100.)
    try:
        reserve(c,token)
        queue.put(True)
    except ServiceError:
        queue.put(False)


def test_cross_process_capacity_fencing(env):
    import multiprocessing
    c,*_=env;ctx=multiprocessing.get_context('spawn');event=ctx.Event();queue=ctx.Queue()
    processes=[ctx.Process(target=_process_reserve,args=(str(c.path),str(i),event,queue)) for i in range(4)]
    for p in processes:p.start()
    event.set()
    results=[queue.get(timeout=10) for _ in processes]
    for p in processes:
        p.join(timeout=10)
        assert p.exitcode==0
    assert sum(results)==2 and len(c.snapshot()['requests'])==2
