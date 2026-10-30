import copy
import pytest
from test_queue_tuning import load
from maas_bench.snapshot import RuntimeFence


def valid():
    return dict(state='COMPLETE',accepted=3,queue_full_rejections=2,target_arrivals=5,actual_completed_calls=5,unsent=[],late_sent_calls=0,not_scheduled_after_stop=0)


def test_pressure_must_really_reject_and_recovery_must_not():
    m=load('http_recovery_continuity.py');r=valid();m.check_point(r)
    with pytest.raises(RuntimeError,match='Fresh recovery'):m.check_point(r,recovery=True)
    r.update(queue_full_rejections=0,target_arrivals=3,actual_completed_calls=3)
    m.check_point(r,recovery=True)
    with pytest.raises(RuntimeError,match='precondition'):m.check_point(r)


@pytest.mark.parametrize('field,value',[('unsent',[1]),('late_sent_calls',1),('accepted',0),('actual_completed_calls',4),('state','FAILED')])
def test_incomplete_measurement_never_recovers_silently(field,value):
    m=load('http_recovery_continuity.py');r=valid();r[field]=value
    with pytest.raises(RuntimeError):m.check_point(r)


@pytest.mark.parametrize('field',['generation','instances','app_containers','parameters'])
def test_boundary_rejects_intervening_reconfiguration(field):
    before={'generation':1,'instances':[1],'app_containers':[2],'parameters':{'replicas':10}}
    after=copy.deepcopy(before);after[field]='changed'
    with pytest.raises(RuntimeError):RuntimeFence(before).check(after)


@pytest.mark.parametrize('drift',[False,True])
def test_no_restore_between_pressure_and_fresh_requests(tmp_path,monkeypatch,drift):
    m=load('http_recovery_continuity.py');tmp_path.chmod(0o700);events=[];captures=[]
    monkeypatch.setattr(m,'read_private',lambda _: {})
    params=dict(replicas=10,executors=12,global_limit=12,project_limit=12,global_queue=25,project_queue=25,vram_claim_bytes=2*1024**3)
    monkeypatch.setattr(m,'proof_entry',lambda path,case,template:({'selected_candidate':{'parameters':params}} if case=='T4-QUEUE' else {'queue_full_observed':True},{}))
    class Probe:
        def capture(self):
            captures.append(True)
            return {'generation':2 if drift and len(captures)>1 else 1,'instances':[1],'app_containers':[2],'parameters':params}
    class FakePhase:
        def __init__(self,*args):self.output=tmp_path;self.probe=Probe();self.status={}
        def point_config(self,point,run):events.append('configure-'+run);return {'pressure':'pressure' in run}
        def completed(self,row):events.append('completed')
        def finish(self,**extra):events.append('restore');self.status.update(state='COMPLETE',**extra)
        def interrupted(self,exc):events.append('interrupted')
        def close(self):events.append('close')
    monkeypatch.setattr(m,'Phase',FakePhase)
    def execute(cfg):
        events.append('request-pressure' if cfg['pressure'] else 'request-fresh');row=valid()
        if not cfg['pressure']:row.update(queue_full_rejections=0,target_arrivals=3,actual_completed_calls=3)
        return row
    monkeypatch.setattr(m,'execute_arrivals',execute)
    cfg={'template_file':'template','queue_proof_file':'queue','reject_proof_file':'reject'}
    if drift:
        with pytest.raises(RuntimeError,match='drift'):m.run(cfg)
        assert 'request-fresh' not in events and 'restore' not in events and 'interrupted' in events
    else:
        assert m.run(cfg)['continuity_verified']
        assert events==['configure-http-recovery-pressure','request-pressure','completed','configure-http-recovery-fresh','request-fresh','completed','restore','close']
