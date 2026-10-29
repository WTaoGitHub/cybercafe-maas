import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
import pytest

ROOT=Path(__file__).resolve().parents[2];sys.path.insert(0,str(ROOT/'scripts/maas/runtime'))
spec=importlib.util.spec_from_file_location('native_tuning',ROOT/'scripts/maas/runtime/native_tuning_matrix.py')
native=importlib.util.module_from_spec(spec);spec.loader.exec_module(native)


def test_native_text_reference_ignores_confidence_noise_but_validates_schema():
    lines=[{'text':'hello','score':.9,'box':[[0,0],[2,0],[2,1],[0,1]]}]
    assert native.text_fingerprint(lines)==hashlib.sha256(b'hello').hexdigest()
    lines[0]['score']=.91
    assert native.text_fingerprint(lines)==hashlib.sha256(b'hello').hexdigest()
    lines[0]['box'][0][0]=float('nan')
    with pytest.raises(ValueError):native.text_fingerprint(lines)


@pytest.mark.parametrize('bad',[None,{},[{'text':'x','score':2,'box':[]}],[{'text':7}]])
def test_invalid_native_output_is_not_a_success(bad):
    with pytest.raises(ValueError):native.text_fingerprint(bad)


def test_native_matrix_requires_matching_complete_baseline_and_exact_scope(tmp_path):
    from maas_bench.snapshot import save_private
    proof=tmp_path/'proof.json';quality=tmp_path/'quality.json'
    proof.write_text(json.dumps({'case_id':'T0-BASELINE','state':'COMPLETE','profile_id':'unit','input_sha256':'input'}))
    save_private(quality,{'profile_id':'unit','input_sha256':'input'})
    config={'baseline_proof_file':str(proof),'baseline_proof_sha256':hashlib.sha256(proof.read_bytes()).hexdigest(),
        'profile_id':'unit','expected_text_file':str(quality),'replica_counts':[4,6,8],'clients':[4,6,8,12]}
    native.verify_prerequisite(config)
    bad=copy.deepcopy(config);bad['clients'].append(32)
    with pytest.raises(ValueError):native.verify_prerequisite(bad)
    proof.write_text('{}')
    with pytest.raises(ValueError):native.verify_prerequisite(config)
