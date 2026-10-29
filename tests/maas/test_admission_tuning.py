import hashlib
import importlib.util
import json
from pathlib import Path
import sys
import threading
from types import SimpleNamespace
import httpx
import pytest
from maas_bench import matrix
from maas_bench.backend import TaskBackend
from maas_bench.sync_load import SyncTraffic
from maas_bench.snapshot import save_private

ROOT=Path(__file__).resolve().parents[2];sys.path.insert(0,str(ROOT/'scripts/maas/runtime'))
spec=importlib.util.spec_from_file_location('admission_tuning',ROOT/'scripts/maas/runtime/admission_tuning.py')
admission=importlib.util.module_from_spec(spec);spec.loader.exec_module(admission)


def test_t3_merges_equivalent_policies_and_keeps_modes_independent():
    base=matrix.load()['baseline'];points=admission.planned([{'parameters':{**base,'replicas':r,'executors':8},'screening_throughput':15} for r in (6,8)])
    assert len(points)==64 and len({p['point_id'] for p in points})==64
    assert {p['mode'] for p in points}=={'async_saturation','sync_prepared','mixed_20','mixed_50'}


def test_adaptive_parameters_require_hashed_same_profile_selection(tmp_path):
    m=matrix.load();proof=tmp_path/'proof.json';proof.write_text(json.dumps({'case_id':'T2-ASYNC','state':'COMPLETE','profile_id':m['profile_id'],'input_sha256':'input'}))
    selection=tmp_path/'selection.json';selection.write_text(json.dumps({'case_id':'T3-LIMITS','profile_id':m['profile_id'],'input_sha256':'input','points':[{'point_id':'selected','parameters':m['baseline'],'mode':'mixed_20'}]}))
    cfg={'case_id':'T3-LIMITS','profile_id':m['profile_id'],'point_id':'selected','parameters':m['baseline'],'load_mode':'mixed_20',
        'selection_file':str(selection),'selection_sha256':hashlib.sha256(selection.read_bytes()).hexdigest(),
        'completed_stages':{'T2-ASYNC':{'state':'COMPLETE','proof_file':str(proof),'proof_sha256':hashlib.sha256(proof.read_bytes()).hexdigest()}}}
    matrix.validate_execution(cfg,'input')
    cfg['load_mode']='sync_prepared'
    with pytest.raises(ValueError):matrix.validate_execution(cfg,'input')


def test_mixed_observation_allows_only_same_project_sync_traffic():
    row=dict(global_queued=1,own_queued=1,running=1,own_running=1,sync_running=1,project_sync_running=1)
    obs=SimpleNamespace(snapshot=lambda _:row)
    backend=TaskBackend(None,obs,[{'input':{'text':'x'}}],allow_sync=True)
    assert backend.snapshot()==row
    row['project_sync_running']=0
    with pytest.raises(RuntimeError):backend.snapshot()


def test_sync_side_traffic_validates_text_and_once_usage_without_replay(tmp_path):
    image=tmp_path/'input.png';image.write_bytes(b'prepared-test-input')
    manifest=tmp_path/'manifest.json';manifest.write_text(json.dumps([{'id':'one','file':image.name,'sha256':hashlib.sha256(image.read_bytes()).hexdigest()}]))
    quality=tmp_path/'quality.json';quality.write_text(json.dumps({'items':{'one':{'text_sha256':hashlib.sha256(b'hello').hexdigest()}}}))
    rows=[]
    obs=SimpleNamespace(scope=('a','e3-test12345:','ocr.default','1'),lock=threading.Lock(),
        connection=SimpleNamespace(execute=lambda *_:SimpleNamespace(fetchall=lambda:list(rows))))
    config={'sync_rate':2,'raw_manifest_file':str(manifest),'expected_text_file':str(quality),'logical_model':'ocr.default'}
    errors=[];events=[];traffic=SyncTraffic(config,obs,errors.append,events.append);traffic.before=set()
    def response(req):
        rows.append({'token':str(len(rows)),'state':'COMPLETED','outcome':'SUCCEEDED','events':1})
        return httpx.Response(200,json={'lines':[{'text':'hello'}]})
    with httpx.Client(base_url='http://test',transport=httpx.MockTransport(response)) as client:
        value=traffic._one(client,0,0)
        assert value['http_status']==200
    assert traffic.stop()['usage_events']==1 and not errors
    assert [r['kind'] for r in events]==['sync_intent','sync_response']
