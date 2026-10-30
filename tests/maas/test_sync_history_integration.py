"""Real test DB/API with substantial old sync history; no GPU inference."""
import time
import psycopg
import pytest
from test_t3_integration import setup,env
from maas_bench.sync_load import SyncTraffic


def test_history_usage_is_not_reconciled_but_current_usage_is(env,monkeypatch,tmp_path):
    client,service,settings,cfg,obs,_=setup(env,monkeypatch,tmp_path)
    try:
        with psycopg.connect(settings.database_url) as db:
            # Historical records intentionally have no usage: irrelevant to the
            # new window, and must not be joined/validated during baseline.
            db.execute("""INSERT INTO sync_activity(token,project,model,state,deadline,outcome)
              SELECT 'history-'||n,'a','ocr.default','COMPLETED',now(),'SUCCEEDED'
              FROM generate_series(1,15000) n""")
        obs.connection.execute('SET statement_timeout=200')
        traffic=SyncTraffic(cfg,obs,lambda e:pytest.fail(type(e).__name__),lambda _:None)
        traffic.before={r['token'] for r in traffic._rows()}
        assert len(traffic.before)==15000
        assert traffic._one(client,0,time.monotonic())['http_status']==200
        # Foreign project/model/probe identities are outside this window.
        with psycopg.connect(settings.database_url) as db:
            for token,project,model,probe in [('foreign','b','ocr.default',False),
                    ('other-model','a','mock.echo',False),('probe','a','ocr.default',True)]:
                db.execute("""INSERT INTO sync_activity(token,project,model,state,deadline,is_probe,outcome)
                  VALUES(%s,%s,%s,'COMPLETED',now(),%s,'SUCCEEDED')""",(token,project,model,probe))
        result=traffic.stop()
        assert result['successes']==result['usage_events']==1
        assert obs.connection.execute('SHOW transaction_read_only').fetchone()['transaction_read_only']=='on'
    finally:obs.close()
