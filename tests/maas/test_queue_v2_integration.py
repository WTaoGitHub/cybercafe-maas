"""Real disposable PG + mock inference; production source/configuration untouched."""
import pytest
from test_integration import env,submit,headers
from maas_bench.backend import DatabaseObserver,TaskBackend
from maas_bench.queue_policy import CompletionClock
from maas_bench.experiment import fresh_namespace
from maas_platform.worker import Worker


def test_completion_observation_is_committed_scoped_and_deadline_checked(env,tmp_path):
    client,s,settings,*_=env;client.headers.update(headers())
    ident=submit(client,'e3-v2-clock-test:0',max_attempts=1).json()['id']
    observer=DatabaseObserver(settings.database_url,'a','mock.echo','1','e3-v2-clock-test:',statement_ms=1000)
    try:
        clock=CompletionClock();clock.observe(observer.snapshot(True));assert not clock.seen
        assert Worker(s).run_once()
        clock.observe(observer.snapshot(True));timings=observer.task_timings()
        assert [r['id'] for r in timings]==[ident];elapsed,_=clock.verify(timings);assert elapsed[0]>=timings[0]['total']
        rows=observer.reconcile([ident]);backend=TaskBackend(client,observer,[{'input':{'text':'hello'}}])
        assert backend.verify_results(rows)['downloaded_results']==1
        assert submit(client,'other-namespace',max_attempts=1).status_code==202;assert Worker(s).run_once()
        assert [r['id'] for r in observer.task_timings()]==[ident]
        with s.db.transaction() as c:c.execute("UPDATE tasks SET deadline=created_at WHERE id=%s",(ident,))
        with pytest.raises(RuntimeError):clock.verify(observer.task_timings())
        with pytest.raises(Exception):observer.connection.execute('UPDATE maintenance SET paused=true')
    finally:observer.close()


def test_existing_namespace_is_rejected_by_readonly_preflight(env,tmp_path):
    client,s,settings,*_=env;run='v2-existing-run123'
    assert submit(client,'e3-'+run+':0',max_attempts=1).status_code==202
    observer=DatabaseObserver(settings.database_url,'a','mock.echo','1','e3-v2-check-test:')
    try:
        with pytest.raises(ValueError,match='namespace'):fresh_namespace({'project':'a'},observer,run,[tmp_path/'new-output'])
        with s.db.transaction() as c:
            assert c.execute('SELECT paused FROM maintenance').fetchone()['paused'] is False
            assert c.execute('SELECT count(*) n FROM tasks').fetchone()['n']==1
    finally:observer.close()


@pytest.mark.parametrize('new_candidate',[False,True])
def test_v2_arrival_measurement_with_real_api_db_and_mock_runtime(env,tmp_path,monkeypatch,new_candidate):
    import copy,json,threading,httpx
    from types import SimpleNamespace
    from test_queue_policy_v2 import setup
    from maas_bench.snapshot import save_private,digest
    import maas_bench.arrival_point as point
    from maas_bench.queue_policy import assess
    client,s,settings,*_=env;settings.max_running=settings.max_project_running=4
    config,policy=setup(tmp_path)
    params=dict(replicas=4,executors=4,global_limit=4,project_limit=4,global_queue=25,project_queue=25,vram_claim_bytes=2147483648)
    fixtures={'fixtures':[{'input':{'text':'mock-input'},'parameters':{}}]};manifest=tmp_path/'fixtures.json';save_private(manifest,fixtures)
    with s.db.transaction() as connection:generation=connection.execute('SELECT generation FROM maintenance').fetchone()['generation']
    snapshot={'profile_id':config['profile_id'],'parameters':params,'input_sha256':digest(fixtures),'generation':generation,'workers':[]}
    before=tmp_path/'snapshot.json';save_private(before,snapshot);dsn=tmp_path/'dsn';dsn.write_text(settings.database_url);dsn.chmod(0o600)
    output=tmp_path/'runs';output.mkdir(mode=0o700)
    config.update(case_id='T4-QUEUE',point_id='T4V2-Q25-burst25',load_mode='bounded_burst',qos_class='bounded_burst',
        arrival_policy={'count':25,'clients':16},parameters=params,fixture_file=str(manifest),snapshot_file=str(before),output_parent=str(output),
        run_id='v2-full-mock-arrival123',source_root=str(tmp_path),model_id=18,logical_model='mock.echo',revision='1',project='a',
        database_dsn_file=str(dsn),customer_key_file=str(settings.bindings_file.parent/'key-a'),base_url='http://test')
    if new_candidate:
        config.update(case_id='C35-Q25-CONFIRM',candidate_queue_protocol='C35-Q25-CONFIRM-V1',candidate_mu_sha256='1'*64,selection_sha256='2'*64)
        monkeypatch.setattr('maas_bench.candidate_queue.validate_execution',lambda *_:policy)
    class Probe:
        def __init__(self,*a):pass
        def capture(self):return copy.deepcopy(snapshot)
        def resources(self,*a):return []
    # Runtime/proof gates are tested separately; this test exercises actual
    # HTTP validation, PG commits, lifecycle and new point timing telemetry.
    monkeypatch.setattr(point.matrix,'validate_execution',lambda *a:None)
    monkeypatch.setattr(point,'ControllerProbe',Probe)
    monkeypatch.setattr('maas_bench.experiment.check_runtime',lambda *a:None)
    monkeypatch.setattr(point,'ResourceGuard',lambda _:SimpleNamespace(controller=lambda:{},check=lambda *a:None))
    original=httpx.Client
    def local_client(**kwargs):
        kwargs.pop('transport',None)
        return original(**kwargs,transport=httpx.MockTransport(lambda req:client.request(req.method,req.url.path,content=req.content,headers=dict(req.headers))))
    monkeypatch.setattr(point.httpx,'Client',local_client)
    stop=threading.Event();errors=[]
    def work():
        try:
            worker=Worker(s,stop_claiming=stop)
            while not stop.is_set():
                if not worker.run_once():stop.wait(.005)
        except Exception as exc:errors.append(type(exc).__name__)
    threads=[threading.Thread(target=work) for _ in range(2)]
    try:
        for thread in threads:thread.start()
        result=point.execute_arrivals(config)
        assert result['state']=='COMPLETE' and result['accepted']==25 and not errors
        assert result['correctness_verified'] and result['observed_completion_p95']>=result['total_p95']
        assert result['wait_max']>=result['wait_p95'] and assess(result,policy)['passed']
        if new_candidate:assert result['query_probe_count']>0 and result['candidate_queue_protocol']=='C35-Q25-CONFIRM-V1'
        assert result['submission_span_seconds']>=0 and result['total_clock_kind']=='DB_UPDATED_AT_NOT_DURABLE_E2E'
    finally:
        stop.set()
        for thread in threads:thread.join(10);assert not thread.is_alive()
