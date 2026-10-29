"""T0 validation only: disposable PostgreSQL and mock model, never real OCR."""
import threading
import time

import pytest

from test_integration import env,headers,submit
from maas_bench.backend import DatabaseObserver,TaskBackend
from maas_bench.contracts import Parameters
from maas_bench.saturation import RunPolicy,SaturationRunner
from maas_platform.worker import Worker


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
