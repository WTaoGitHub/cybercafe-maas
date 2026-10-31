import copy
import json
import threading
from types import SimpleNamespace
import pytest
from maas_bench.snapshot import digest,save_private
from maas_bench.t6_preparation import (validate_pair,load_refresh,file_sha,
    forecast_bytes,require_budget,require_inputs,install_stop_handlers)


def manifest(n,prefix):
    return {'profile_id':'test','fixtures':[{'id':str(i),'sha256':format(i,'064x'),
        'input':{'image':{'artifact_id':prefix+str(i)}},'parameters':{}} for i in range(n)]}


def quality(m):
    return {'profile_id':'test','input_sha256':digest(m),'items':{r['id']:{'text_sha256':'text'} for r in m['fixtures']}}


def test_rebinding_preserves_source_and_all_non_reference_content():
    old=manifest(3,'old');new=manifest(3,'new');frozen=copy.deepcopy(old)
    validate_pair(old,new,quality(old),quality(new));assert old==frozen
    for mutate in (lambda x:x['fixtures'].reverse(),lambda x:x['fixtures'][0]['parameters'].update(angle=90),
                   lambda x:x['fixtures'][0].update(sha256='f'*64),lambda x:x.update(profile_id='other')):
        changed=copy.deepcopy(new);mutate(changed)
        with pytest.raises(ValueError,match='content'):validate_pair(old,changed,quality(old),quality(changed))


def test_rebinding_rejects_quality_changes_and_reused_ids():
    old=manifest(2,'old');new=manifest(2,'new');q=quality(new);q['items']['0']['text_sha256']='changed'
    with pytest.raises(ValueError,match='quality'):validate_pair(old,new,quality(old),q)
    new['fixtures'][1]['input']['image']['artifact_id']='new0'
    with pytest.raises(ValueError,match='reused'):validate_pair(old,new,quality(old),quality(new))
    with pytest.raises(ValueError,match='fresh'):validate_pair(old,old,quality(old),quality(old))


@pytest.fixture
def receipt(tmp_path):
    tmp_path.chmod(0o700)
    def put(name,value):
        p=tmp_path/name;save_private(p,value);return str(p)
    old=manifest(40,'old');mix=manifest(50,'old');new=manifest(40,'new');newmix=manifest(50,'new')
    t={'profile_id':'test','fixture_file':put('old.json',old),'expected_text_file':put('oldq.json',quality(old))}
    claim={'mixed_workload_fixture_file':put('mix.json',mix),'mixed_workload_sha256':digest(mix),'mixed_quality_file':put('mixq.json',quality(mix))}
    claimfile=put('claim.json',claim);r={'state':'COMPLETE','profile_id':'test','claim_proof_sha256':file_sha(claimfile)}
    for kind,source,target in (('ordinary',old,new),('mixed',mix,newmix)):
        q=put(kind+'q.json',quality(target));r[kind]={'source_sha256':digest(source),'fixture_file':put(kind+'.json',target),'sha256':digest(target),'quality_file':q,'quality_file_sha256':file_sha(q)}
    p=put('receipt.json',r);c={'claim_proof_file':claimfile,'fixture_refresh_file':p,'fixture_refresh_sha256':file_sha(p)}
    return c,t,claim,r


def test_receipt_binds_both_original_proof_and_actual_new_inputs(receipt):
    c,t,claim,r=receipt;before=file_sha(t['fixture_file']);loaded,sets=load_refresh(c,t,claim)
    assert len(sets['mixed']['fixtures'])==50 and before==file_sha(t['fixture_file'])
    c['fixture_refresh_sha256']='0'*64
    with pytest.raises(ValueError,match='receipt'):load_refresh(c,t,claim)


def test_modified_new_manifest_is_not_silently_blessed(receipt):
    c,t,claim,r=receipt;p=r['ordinary']['fixture_file']
    with open(p,'w') as f:json.dump(manifest(40,'other'),f)
    with pytest.raises(ValueError,match='binding'):load_refresh(c,t,claim)


def test_whole_stage_budget_has_margin_and_no_future_gc_credit():
    f=forecast_bytes(20,7000,9000)
    assert f['total_bytes']==f['confirmation_bytes']+f['soak_bytes']
    assert f['forecast_margin']==1.25 and f['future_expiry_credit_bytes']==0
    require_budget(100,700,1000,10**10)
    with pytest.raises(RuntimeError,match='insufficient'):require_budget(200,701,1000,10**10)
    with pytest.raises(RuntimeError):require_budget(0,10,1000,2*1024**3)
    for v in (0,-1,float('inf'),float('nan')):
        with pytest.raises(ValueError):forecast_bytes(v,1,1)


def test_entire_input_set_must_cover_global_finish_deadline():
    m=manifest(2,'new');rows=[{'id':'new'+str(i),'sha256':format(i,'064x'),'expiry':1000,'deleted_at':None} for i in range(2)]
    o=SimpleNamespace(lock=threading.Lock(),scope=('project',),connection=SimpleNamespace(execute=lambda *a:SimpleNamespace(fetchall=lambda:rows)))
    require_inputs(o,{'mixed':m},900)
    rows[0]['expiry']=899
    with pytest.raises(RuntimeError,match='lifetime'):require_inputs(o,{'mixed':m},900)
    rows[0]['expiry']=1000;rows[1]['deleted_at']='deleted'
    with pytest.raises(RuntimeError):require_inputs(o,{'mixed':m},900)


def test_background_driver_stop_handlers_do_not_inherit_ignore(monkeypatch):
    import signal
    handlers={};monkeypatch.setattr(signal,'signal',lambda sig,handler:handlers.update({sig:handler}))
    install_stop_handlers()
    assert set(handlers)=={signal.SIGINT,signal.SIGTERM}
    for handler in handlers.values():
        with pytest.raises(KeyboardInterrupt):handler(None,None)


@pytest.mark.parametrize('filename',['confirmation_tuning.py','soak_tuning.py'])
def test_failed_reservation_precedes_any_parameter_change(monkeypatch,filename):
    from test_queue_tuning import load
    m=load(filename);calls=[]
    cfg={'template_file':'template','claim_proof_file':'claim','confirmation_proof_file':'confirm',
        'baseline_parameters_file':'baseline','fixture_refresh_sha256':'refresh','preparation_sha256':'reservation','output_directory':'out'}
    claim={'final_candidates':[{'parameters':{'replicas':10}}]}
    confirmed={'selected_candidate':{'parameters':{'replicas':10}},'fixture_refresh_sha256':'refresh','preparation_sha256':'reservation'}
    monkeypatch.setattr(m,'read_private',lambda p:{'parameters':{}} if p=='baseline' else {})
    monkeypatch.setattr(m,'proof_entry',lambda p,*a:(claim if p=='claim' else confirmed,{}))
    monkeypatch.setattr(m,'workload_extra',lambda *a:{'workload_sha256':'workload'})
    class Phase:
        observer=object()
        def __init__(self,*a,**k):calls.append('created')
        def point_config(self,*a,**k):pytest.fail('Parameter mutation before reservation')
        def interrupted(self,exc):calls.append('interrupted')
        def close(self):calls.append('closed')
    monkeypatch.setattr(m,'Phase',Phase)
    def refuse(*a):raise RuntimeError('reservation blocked')
    monkeypatch.setattr(m,'validate_reservation',refuse)
    with pytest.raises(RuntimeError,match='reservation blocked'):m.run(cfg)
    assert calls==['created','interrupted','closed']


def test_actual_backend_fixtures_use_refreshed_ids_and_original_text_reference(receipt,tmp_path):
    from maas_bench.t6_preparation import workload_extra
    from maas_bench.fixtures import prepared_fixtures
    c,t,claim,r=receipt;t['raw_manifest_file']='raw'
    path=tmp_path/'template.json';save_private(path,t);c['template_file']=str(path)
    extra=workload_extra(c,t,claim,'ordinary')
    fixtures,scope=prepared_fixtures({**t,**extra},json.loads(open(t['fixture_file']).read()))
    assert fixtures[0]['input']['image']['artifact_id']=='new0'
    assert fixtures[0]['expected_text_sha256']=='text' and len(fixtures)==40


def test_pipeline_does_not_launch_confirmation_when_preparation_is_blocked(tmp_path,monkeypatch):
    from test_queue_tuning import load
    m=load('run_t6.py');tmp_path.chmod(0o700)
    monkeypatch.setattr(m.prepare_t6,'run',lambda c:{'state':'BLOCKED_STORAGE'})
    monkeypatch.setattr(m.confirmation_tuning,'run',lambda c:pytest.fail('Must not launch confirmation'))
    monkeypatch.setattr(m.soak_tuning,'run',lambda c:pytest.fail('Must not launch soak'))
    result=m.run({'output_directory':str(tmp_path/'pipeline')})
    assert result['state']=='BLOCKED_STORAGE' and not result['formal_confirmation_started']
