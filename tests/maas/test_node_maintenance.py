import copy,json
import pytest
from maas_platform.node_maintenance import NodeMaintenance


class Environment:
    def __init__(self,tmp_path):
        self.w={'id':2,'name':'XZ-31-002','labels':{'worker-name':'XZ-31-002'},'maintenance':None,'state':'ready','cluster_id':1}
        self.ms=[{'id':1,'name':'ocr','cluster_id':1,'replicas':2,'backend':'ocr','env':{'a':'1'}}]
        self.status={'paused':False,'managed_requests_drained':False,'generation':0}
        self.unknown=False;self.stopped=False;self.puts=[];self.actions=[]
        self.c=NodeMaintenance(self.api,self.tasks,self.node,tmp_path/'journal.json',wait_seconds=.1,sleep=lambda _:None)
    def api(self,path,body=None,method=None):
        if path=='/version':return {'version':'v2.2.3'}
        if path.startswith('/v2/models?'):return {'items':copy.deepcopy(self.ms)}
        if path=='/v2/workers/2':
            if method=='PUT':
                self.puts.append(path);self.w.update(body);self.w['state']='maintenance' if (body['maintenance'] or {}).get('enabled') else 'ready'
            return copy.deepcopy(self.w)
        if path=='/v2/models/1/instances':
            return {'items':[{'id':i,'state':'running','worker_name':'XZ-31-002'} for i in range(self.ms[0]['replicas'])]}
        if path=='/v2/models/1':
            if method=='PUT':self.puts.append(path);self.ms[0]=copy.deepcopy(body)
            return copy.deepcopy(self.ms[0])
        raise AssertionError(path)
    def tasks(self,action,reason=None):
        if action=='pause':self.status.update(paused=True,generation=self.status['generation']+1,managed_requests_drained=not self.unknown)
        if action=='resume':self.status.update(paused=False,managed_requests_drained=False);return {'real_probe_passed':True}
        value=copy.deepcopy(self.status)
        if self.unknown:value['backend_activity']=[{'state':'UNKNOWN'}]
        return value
    def node(self,action):
        self.actions.append(action)
        if action=='status':return {'running_workload_containers':['wta-gpustack-worker']}
        if action=='stop':self.stopped=True;return {'backend_stop_observed':True}
        if action=='restore':self.stopped=False
        return {'ok':True}


def test_journaled_maintenance_is_not_stop_and_recovery_restores_replicas(tmp_path):
    e=Environment(tmp_path);s=e.c.begin(2,[1],'test window')
    assert s['instances_after_maintenance']['1'] and e.ms[0]['replicas']==2
    assert not e.stopped and e.w['maintenance']['enabled']
    assert e.c.stop()['phase']=='STOPPED' and e.ms[0]['replicas']==0
    assert e.c.recover()['phase']=='RECOVERED' and e.ms[0]['replicas']==2
    assert e.w['maintenance'] is None and e.status['paused']
    assert e.c.resume()['phase']=='RESUMED'
    assert e.actions==['arm','status','stop','restore','disarm']


def test_unknown_refuses_any_deployment_mutation(tmp_path):
    e=Environment(tmp_path);e.unknown=True
    with pytest.raises(RuntimeError,match='drained'):e.c.begin(2,[1],'unknown')
    assert not e.puts and not e.actions and e.status['paused']


@pytest.mark.parametrize('fault',['generation','config','new-model','worker-maintenance','replicas'])
def test_drift_prevents_stop_mutations(tmp_path,fault):
    e=Environment(tmp_path);e.c.begin(2,[1],'test');e.puts=[]
    if fault=='generation':e.status['generation']+=1
    if fault=='config':e.ms[0]['env']['a']='changed'
    if fault=='new-model':e.ms.append({'id':2,'cluster_id':1})
    if fault=='worker-maintenance':e.w['maintenance']={'enabled':True,'message':'another window'}
    if fault=='replicas':e.ms[0]['replicas']=7
    with pytest.raises(RuntimeError):e.c.stop()
    assert not e.puts and not e.stopped


def test_partial_scale_recovery_keeps_admission_paused(tmp_path):
    e=Environment(tmp_path);e.c.begin(2,[1],'test');e.ms[0]['replicas']=0
    e.c.event('SCALING_DOWN')
    restarted=NodeMaintenance(e.api,e.tasks,e.node,e.c.path,wait_seconds=.1,sleep=lambda _:None)
    assert restarted.recover()['phase']=='RECOVERED'
    assert e.ms[0]['replicas']==2 and e.status['paused']


def test_scope_and_journal_cannot_be_silently_replaced(tmp_path):
    e=Environment(tmp_path)
    with pytest.raises(RuntimeError):e.c.begin(2,[],'wrong scope')
    e.c.begin(2,[1],'test')
    with pytest.raises(RuntimeError,match='already exists'):e.c.begin(2,[1],'overwrite')


def test_failed_stop_proof_never_enters_stopped_phase(tmp_path):
    e=Environment(tmp_path);e.c.begin(2,[1],'test')
    e.c.node=lambda action:{'backend_stop_observed':False,'running_workload_containers':[]}
    with pytest.raises(RuntimeError,match='no stop proof'):e.c.stop()
    assert e.c.state()['phase']=='STOP_UNCONFIRMED' and e.status['paused']


def test_recovery_reconciles_spread_without_changing_model_spec(tmp_path):
    e=Environment(tmp_path);original_api=e.api;packed=False
    def api(path,body=None,method=None):
        nonlocal packed
        if path.startswith('/v2/model-instances/'):
            assert method=='DELETE';packed=False;return {}
        value=original_api(path,body,method)
        if path.endswith('/instances'):
            for i,x in enumerate(value['items']):x['worker_name']='XZ-31-001' if packed or i==0 else 'XZ-31-002'
        return value
    e.c.api=api;e.c.begin(2,[1],'test');e.c.stop();packed=True
    state=e.c.recover()
    assert state['phase']=='RECOVERED' and len(state['placement_repairs'])==1 and e.status['paused']


def test_failed_probe_does_not_mark_session_resumed(tmp_path):
    e=Environment(tmp_path);e.c.begin(2,[1],'test');e.c.stop();e.c.recover()
    original=e.c.tasks
    def tasks(action,*args):
        if action=='resume':raise RuntimeError('real inference probe failed')
        return original(action,*args)
    e.c.tasks=tasks
    with pytest.raises(RuntimeError,match='probe failed'):e.c.resume()
    assert e.c.state()['phase']=='RECOVERED' and e.status['paused']
