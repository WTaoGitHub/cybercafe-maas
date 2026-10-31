import copy
from types import SimpleNamespace
import pytest
from maas_bench.claim_transition import validate_evidence,validate_target,change_claim
from maas_bench.contracts import Parameters


def fixture():
    source={'profile_id':'p','input_sha256':'s','workers':[{'id':1},{'id':2}], 'catalog_sha256':'c','app_fixed_env_sha256':'e','gpustack_version':'2.2.3','model':{'id':1,'replicas':8,'env':{'GPUSTACK_MODEL_VRAM_CLAIM':str(2*1024**3),'threads':'2'}},'app_containers':[{'service':'api','image':'same'}]}
    receipt={'state':'PROFILE_VERIFIED','measurement_window_verified':True,'covered_instances':8,'candidate_claim_bytes':int(3.5*1024**3),'max_sampled_container_bytes':2928*1024**2}
    fit={'state':'COMPLETE','native_ready_instances':8,'placement':[4,4],'computed_claim_bytes_per_instance':int(3.5*1024**3)}
    return receipt,fit,source,copy.deepcopy(source)


def test_native_fit_and_measurement_bind_exact_budget_and_hardware():
    r,f,s,c=fixture();assert validate_evidence(r,f,s,c)==Parameters(8,8,8,8,25,25,int(3.5*1024**3))
    for key in ('workers','catalog_sha256','input_sha256','app_fixed_env_sha256'):
        bad=copy.deepcopy(c);bad[key]='drift'
        with pytest.raises(ValueError,match='drift'):validate_evidence(r,f,s,bad)
    c['model']['env']['threads']='3'
    with pytest.raises(ValueError,match='model drift'):validate_evidence(r,f,s,c)


def test_failed_placement_unknown_measurement_or_budget_fudging_never_authorizes():
    for key,value in (('measurement_window_verified',False),('candidate_claim_bytes',2*1024**3),('covered_instances',7)):
        r,f,s,c=fixture();r[key]=value
        with pytest.raises(ValueError):validate_evidence(r,f,s,c)
    r,f,s,c=fixture();f['placement']=[3,5]
    with pytest.raises(ValueError,match='placement'):validate_evidence(r,f,s,c)


def test_only_candidate_or_precisely_captured_original_can_be_requested():
    original=Parameters(6,4,4,4,1000,100).to_dict();controller=SimpleNamespace(original={'parameters':original})
    candidate=Parameters(8,8,8,8,25,25,int(3.5*1024**3))
    validate_target(controller,candidate.vram_claim_bytes,8,candidate,False)
    validate_target(controller,original['vram_claim_bytes'],6,None,True)
    with pytest.raises(ValueError,match='captured'):validate_target(controller,2*1024**3,4,None,True)
    with pytest.raises(ValueError,match='evidence-bound'):validate_target(controller,2*1024**3,8,candidate,False)


def test_runtime_drift_rejected_before_any_native_mutation():
    controller=SimpleNamespace(expected={'generation':1},probe=SimpleNamespace(capture=lambda:{'generation':2}))
    candidate=Parameters(8,8,8,8,25,25,int(3.5*1024**3))
    with pytest.raises(RuntimeError,match='drift'):change_claim(controller,candidate.vram_claim_bytes,8,evidence=candidate)


def simulated_controller(tmp_path,monkeypatch,fail_claim=False):
    import json,sys
    from maas_bench.controller import fixed_profile
    p=Parameters(4,4,4,4,1000,100).to_dict()
    s=fixture()[2];s['model'].update(replicas=4,worker_selector={'pool':'test'});s.update(parameters=p,generation=10)
    s['model']['env']['GPUSTACK_MODEL_VRAM_CLAIM']=str(p['vram_claim_bytes']);state=copy.deepcopy(s);actions=[]
    live=tmp_path/'live';(live/'maintenance').mkdir(parents=True)
    journal=tmp_path/'maintenance.json';journal.write_text(json.dumps({'phase':'RESUMED'}));cfg=tmp_path/'config.json';cfg.write_text(json.dumps({'journal':str(journal)}))
    (live/'maintenance/active.json').write_text(json.dumps({'config':str(cfg)}))
    def api(path,desired=None,method=None):
        if path.endswith('/instances'):
            return {'items':[{'state':'running','worker_id':1+i%2,'computed_resource_claim':{'vram':{'0':int(state['model']['env']['GPUSTACK_MODEL_VRAM_CLAIM'])}}} for i in range(state['model']['replicas'])]}
        if desired is not None:
            actions.append(('put',desired['replicas'],desired['env']['GPUSTACK_MODEL_VRAM_CLAIM']))
            if fail_claim and int(desired['env']['GPUSTACK_MODEL_VRAM_CLAIM'])!=p['vram_claim_bytes']:raise RuntimeError('uncertain native update')
            state['model']=copy.deepcopy(desired)
        return copy.deepcopy(state['model'])
    def cli(command,*args):
        actions.append((command,))
        if command in ('pause','resume'):state['generation']+=1
        return {'generation':state['generation'],'managed_requests_drained':True}
    def capture():
        state['parameters']={**p,'replicas':state['model']['replicas'],'vram_claim_bytes':int(state['model']['env']['GPUSTACK_MODEL_VRAM_CLAIM'])}
        return copy.deepcopy(state)
    def scale(api,expected,count,selector):
        desired=copy.deepcopy(expected);desired['replicas']=count;return api('/model',desired,'PUT')
    monkeypatch.setitem(sys.modules,'replica_capacity_matrix',SimpleNamespace(change_model=scale))
    monkeypatch.setattr('maas_bench.claim_transition.observe_resources',lambda *args:None)
    out=tmp_path/'ops';out.mkdir(mode=0o700)
    c=SimpleNamespace(original=copy.deepcopy(s),expected=copy.deepcopy(s),probe=SimpleNamespace(capture=capture),quiet=lambda:None,
        check_fixed=lambda snapshot:None,pool=SimpleNamespace(LOCK=tmp_path/'lock',LIVE=live,cli=cli),api=api,output=out)
    return c,state,actions


def test_claim_transition_rebuilds_only_owned_model_and_acknowledges_exact_change(tmp_path,monkeypatch):
    c,state,actions=simulated_controller(tmp_path,monkeypatch);target=Parameters(8,8,8,8,25,25,int(3.5*1024**3))
    after=change_claim(c,target.vram_claim_bytes,8,evidence=target)
    assert after['generation']==12 and after['parameters']['replicas']==8 and after['parameters']['vram_claim_bytes']==target.vram_claim_bytes
    assert [x[1] for x in actions if x[0]=='put']==[0,0,8]
    assert actions[-1]==('resume',) and c.original['parameters']['replicas']==4
    restored=change_claim(c,2*1024**3,4,restoring=True)
    assert restored['generation']==14 and restored['parameters']==c.original['parameters']


def test_uncertain_claim_update_never_replays_restores_or_resumes(tmp_path,monkeypatch):
    import json
    c,state,actions=simulated_controller(tmp_path,monkeypatch,True);target=Parameters(8,8,8,8,25,25,int(3.5*1024**3))
    with pytest.raises(RuntimeError,match='uncertain'):change_claim(c,target.vram_claim_bytes,8,evidence=target)
    assert state['model']['replicas']==0 and state['generation']==11
    assert not any(x[0]=='resume' for x in actions) and sum(x[0]=='put' for x in actions)==2
    assert json.loads(next(c.output.glob('*-claim.json')).read_text())['state']=='INTERRUPTED_REQUIRES_INSPECTION'
