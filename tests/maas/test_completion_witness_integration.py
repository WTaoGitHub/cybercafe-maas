"""Disposable real PG/API + mock inference; never a live capacity proof."""
import threading
import time
from types import SimpleNamespace
import pytest
from test_integration import env,headers,submit
from maas_bench.backend import DatabaseObserver
from maas_bench.completion_witness import CompletionWitness
from maas_platform.worker import Worker


def test_committed_witness_real_pg_scope_and_deadline(env):
    client,s,settings,*_=env;client.headers.update(headers());events=[];errors=[]
    observer=DatabaseObserver(settings.database_url,'a','mock.echo','1','e3-witness-real123:',statement_ms=1000)
    witness=CompletionWitness(observer,events.append,errors.append)
    try:
        witness.start();ident=submit(client,'e3-witness-real123:0',max_attempts=1).json()['id'];assert Worker(s).run_once()
        assert witness.verify([ident])['all_accepted_observed_before_deadline'] and not errors
        assert events[-1]['observations'][0]['task_id']==ident
        with s.db.transaction() as c:c.execute('UPDATE tasks SET deadline=created_at WHERE id=%s',(ident,))
        with pytest.raises(RuntimeError,match='deadline'):witness.verify([ident])
    finally:witness.stop();observer.close()


def test_new_saturated_entry_wires_witness_and_never_uses_legacy_matrix(env,tmp_path,monkeypatch):
    import copy,json,httpx
    import maas_bench.__main__ as main
    from maas_bench.snapshot import save_private,digest
    from test_queue_policy_v2 import setup
    client,s,settings,*_=env;settings.max_running=settings.max_project_running=4
    cfg,_=setup(tmp_path);fixtures={'fixtures':[{'input':{'text':'mock-input'},'parameters':{}}]};file=tmp_path/'manifest.json';save_private(file,fixtures)
    with s.db.transaction() as c:generation=c.execute('SELECT generation FROM maintenance').fetchone()['generation']
    params=dict(replicas=4,executors=4,global_limit=4,project_limit=4,global_queue=25,project_queue=25,vram_claim_bytes=2*1024**3)
    snapshot={'profile_id':cfg['profile_id'],'input_sha256':digest(fixtures),'parameters':params,'generation':generation,'workers':[]};before=tmp_path/'snapshot.json';save_private(before,snapshot)
    dsn=tmp_path/'dsn';dsn.write_text(settings.database_url);dsn.chmod(0o600);out=tmp_path/'runs';out.mkdir(mode=0o700)
    cfg.update(candidate_t6_protocol='C35-T6-V1',case_id='T6-C35',point_id='test-wiring',run_id='witness-main-real123',parameters=params,
        fixture_file=str(file),snapshot_file=str(before),output_parent=str(out),source_root=str(tmp_path),model_id=18,logical_model='mock.echo',revision='1',project='a',
        database_dsn_file=str(dsn),customer_key_file=str(settings.bindings_file.parent/'key-a'),base_url='http://test',policy={'seconds':300,'max_seconds':300,'min_successes':200},
        private_mac_backup_receipt_sha256='a'*64,selection_file_sha256='b'*64,queue_proof_file_sha256='c'*64)
    monkeypatch.setattr('maas_bench.candidate_t6.validate_execution',lambda *_:None)
    monkeypatch.setattr(main.matrix,'validate_execution',lambda *_:pytest.fail('New protocol used legacy validator'))
    class Probe:
        def __init__(self,*_):pass
        def capture(self):return copy.deepcopy(snapshot)
        def resources(self,*_):return []
    monkeypatch.setattr('maas_bench.runtime.ControllerProbe',Probe);monkeypatch.setattr('maas_bench.experiment.check_runtime',lambda *_:None)
    monkeypatch.setattr('maas_bench.resources.ResourceGuard',lambda _:SimpleNamespace(controller=lambda:{},check=lambda *_:None))
    original=httpx.Client
    def local_client(**kwargs):
        kwargs.pop('transport',None);return original(**kwargs,transport=httpx.MockTransport(lambda req:client.request(req.method,req.url.path,content=req.content,headers=dict(req.headers))))
    monkeypatch.setattr(httpx,'Client',local_client)
    # The small deterministic runner tests wiring, commits and cleanup only.
    # It never produces a formal measurement or shortens a production window.
    class MiniRunner:
        def __init__(self,backend,*_):self.backend=backend;self.failure=None
        def _fail(self,error):self.failure=error
        def run(self):
            ids=[self.backend.submit(i) for i in range(3)];until=time.monotonic()+5
            while True:
                rows=self.backend.reconcile(ids)
                if len(rows)==3 and all(r['state']=='SUCCEEDED' for r in rows):break
                if time.monotonic()>until:raise RuntimeError('Test workers did not finish')
                time.sleep(.01)
            assert self.backend.verify_results(rows)['downloaded_results']==3
            return {'state':'COMPLETE','accepted_task_ids':ids,'parameters':params}
    monkeypatch.setattr('maas_bench.saturation.SaturationRunner',MiniRunner)
    stop=threading.Event();errors=[]
    def work():
        try:
            worker=Worker(s,stop_claiming=stop)
            while not stop.is_set():
                if not worker.run_once():stop.wait(.005)
        except Exception as exc:errors.append(type(exc).__name__)
    thread=threading.Thread(target=work);thread.start()
    try:
        assert main.execute(cfg)['state']=='COMPLETE' and not errors
        raw=json.loads((out/cfg['run_id']/'summary.json').read_text());assert raw['completion_witness']['all_accepted_observed_before_deadline'] and raw['candidate_t6_protocol']=='C35-T6-V1'
        assert not any(t.name=='bench-committed-success-witness' and t.is_alive() for t in threading.enumerate())
    finally:stop.set();thread.join(10);assert not thread.is_alive()
