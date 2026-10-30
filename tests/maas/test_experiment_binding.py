import copy
import hashlib
import json
from pathlib import Path
import pytest
from maas_bench import matrix
from maas_bench.experiment import matrix_for,stamp,check_proof,check_images

ROOT=Path(__file__).resolve().parents[2]


def config(tmp_path):
    source=ROOT/'docs/framework/E3-TUNING-GC-R1-MATRIX.json';path=tmp_path/'matrix.json';path.write_bytes(source.read_bytes())
    return {'matrix_file':str(path),'matrix_sha256':hashlib.sha256(path.read_bytes()).hexdigest(),'profile_id':json.loads(path.read_text())['profile_id']}


def test_explicit_profile_matrix_and_runtime_stamp(tmp_path):
    c=config(tmp_path);m=matrix_for(c);metadata=stamp(c)
    assert m['profile_id']!=matrix.load()['profile_id'] and len(metadata)==2
    check_proof(metadata,c)
    with pytest.raises(ValueError):check_proof({},c)
    images=[(s,m['runtime_binding']['application_image_id']) for s in ('api','executor','observer')];check_images(c,images)
    with pytest.raises(ValueError):check_images(c,images[:-1])
    with pytest.raises(ValueError):check_images(c,[('api','sha256:'+'0'*64),*images[1:]])


@pytest.mark.parametrize('fault',['sha','missing_hash','profile','dependency','image'])
def test_invalid_binding_is_rejected(tmp_path,fault):
    c=config(tmp_path);m=json.loads(Path(c['matrix_file']).read_text())
    if fault=='sha':c['matrix_sha256']='0'*64
    elif fault=='missing_hash':c.pop('matrix_sha256')
    elif fault=='profile':c['profile_id']='old-profile'
    else:
        if fault=='dependency':next(s for s in m['stages'] if s['case_id']=='T4-QUEUE')['depends_on']=[]
        else:m['runtime_binding']['application_image_id']='mutable-tag'
        Path(c['matrix_file']).write_text(json.dumps(m));c['matrix_sha256']=hashlib.sha256(Path(c['matrix_file']).read_bytes()).hexdigest()
    with pytest.raises(ValueError):matrix_for(c)


def test_new_profile_point_rejects_old_profile_proof_even_if_marked_complete(tmp_path):
    c=config(tmp_path);m=matrix_for(c);point=matrix.plan(m,case_ids=['T2-ASYNC'])['stages'][0]['points'][0]
    proof=tmp_path/'proof.json';v={'case_id':'T1-NATIVE','state':'COMPLETE','profile_id':matrix.load()['profile_id'],'input_sha256':'input',**stamp(c)};proof.write_text(json.dumps(v))
    c.update(case_id='T2-ASYNC',point_id=point['point_id'],parameters={k:v for k,v in point.items() if k!='point_id'},completed_stages={'T1-NATIVE':{'state':'COMPLETE','proof_file':str(proof),'proof_sha256':hashlib.sha256(proof.read_bytes()).hexdigest()}})
    with pytest.raises(ValueError):matrix.validate_execution(c,'input')
    v['profile_id']=c['profile_id'];proof.write_text(json.dumps(v));c['completed_stages']['T1-NATIVE']['proof_sha256']=hashlib.sha256(proof.read_bytes()).hexdigest()
    matrix.validate_execution(c,'input')
    v.pop('runtime_binding_sha256');proof.write_text(json.dumps(v));c['completed_stages']['T1-NATIVE']['proof_sha256']=hashlib.sha256(proof.read_bytes()).hexdigest()
    with pytest.raises(ValueError):matrix.validate_execution(c,'input')


def test_verified_baseline_import_is_metadata_only_and_cannot_relabel_old_profile(tmp_path):
    import importlib.util
    path=ROOT/'scripts/maas/runtime/bind_baseline_proof.py';spec=importlib.util.spec_from_file_location('bind_baseline',path);module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    c=config(tmp_path);p=tmp_path/'verification.json';source=ROOT/'results/maas-artifact-gc-20261030/baseline.json';p.write_bytes(source.read_bytes())
    c.update(verification_file=str(p),verification_sha256=hashlib.sha256(p.read_bytes()).hexdigest())
    proof=module.bind(c);assert proof['state']=='COMPLETE' and proof['case_id']=='T0-BASELINE' and proof['source_kind']=='verified_baseline_import_no_new_measurement'
    v=json.loads(p.read_text());v['profile_id']='old';p.write_text(json.dumps(v));c['verification_sha256']=hashlib.sha256(p.read_bytes()).hexdigest()
    with pytest.raises(ValueError):module.bind(c)
