import copy
import pytest
from maas_bench.candidate_qualification import planned,reference_rates
from maas_bench.contracts import Parameters


def test_frozen_new_tuple_plan_requires_resource_gate_then_two_workload_references():
    rows=planned();assert len(rows)==7
    assert rows[0]=={'point_id':'resource-qualification','workload':'mixed','seconds':300,'phase':'resource','idle_seconds':60}
    assert [(r['workload'],r['seconds']) for r in rows[1:]]==[('ordinary',60)]*3+[('mixed',60)]*3


def test_mu_cannot_borrow_other_workload_or_old_claim_configuration():
    p=Parameters(8,8,8,8,25,25,int(3.5*1024**3)).to_dict();rows=[]
    for i,x in enumerate(planned()):
        rate=16 if x['workload']=='ordinary' else 12
        rows.append({**x,'run_id':'new-'+str(i),'state':'COMPLETE','parameters':p,'measurement_seconds':60,'measured_successes':rate*60,'throughput_per_second':rate,
            'resource_qualified':i==0,'supply':{'sample_count':700,'ready_queue_min':8,'empty_events':[],'max_observed_sample_gap_seconds':.1}})
    assert reference_rates(rows,p)=={'ordinary':16,'mixed':12}
    bad=copy.deepcopy(rows);bad[0]['resource_qualified']=False
    with pytest.raises(ValueError,match='resource qualification'):reference_rates(bad,p)
    bad=copy.deepcopy(rows);bad[-1]['parameters']['vram_claim_bytes']=2*1024**3
    with pytest.raises(ValueError):reference_rates(bad,Parameters(8,8,8,8,25,25,int(3.5*1024**3)).to_dict())
    with pytest.raises(ValueError):reference_rates(rows[:-1],p)


def test_entry_rejects_unscoped_execution_or_missing_backup_before_access():
    from test_queue_tuning import load
    m=load('qualify_candidate.py')
    with pytest.raises(ValueError,match='scope'):m.run({'scope':'T6'})
    with pytest.raises(ValueError,match='backup'):m.run({'scope':'NEW_CLAIM_RESOURCE_AND_MU_NOT_T6'})


def test_evidence_hash_tampering_is_rejected(tmp_path):
    import hashlib,json
    from test_queue_tuning import load
    m=load('qualify_candidate.py');p=tmp_path/'receipt.json';p.write_text('{}');p.chmod(0o600)
    cfg={'receipt':str(p),'receipt_sha256':hashlib.sha256(p.read_bytes()).hexdigest()}
    assert m.bound_file(cfg,'receipt')=={}
    p.write_text('{"changed":true}')
    with pytest.raises(ValueError,match='changed'):m.bound_file(cfg,'receipt')
