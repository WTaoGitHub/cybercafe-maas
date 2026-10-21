"""Deterministic E1 local acceptance using an in-process HTTP test transport.

No SSH, credentials, external HTTP, Docker, deployment or real GPU operations.
"""
import argparse
import asyncio
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace

sys.path.insert(0,str(Path(__file__).resolve().parents[2]))
import httpx
from maas_platform.e1.control import E1Control
from maas_platform.e1.gateway import E1Gateway
from maas_platform.e1.topology import Replica
from maas_platform.errors import ServiceError


def run():
    image='sha256:'+'a'*64
    clock=lambda:100.
    scenarios=[];sends=[];node_actions=[]
    with tempfile.TemporaryDirectory(prefix='maas-e1-local-') as temp:
        c=E1Control.create(Path(temp)/'journal.json',{
            'model1':{'revision':'r1','image_id':image,'workers':['001','002']},
            'model2':{'revision':'r1','image_id':image,'workers':['001']}},
            {'001':2,'002':1},global_slots=3,project_slots=2,recovery_hold_seconds=0,clock=clock)
        rows=[Replica(m,'r1',w,i,m+'-'+w,image,1,100.,str(i))
              for i,(m,w) in enumerate([('model1','001'),('model1','002'),('model2','001')],1)]
        c.publish(rows)
        def upstream(request):
            route=json.loads(request.content)['model'];sends.append(route)
            if route=='model1-002' and fail[0]:raise httpx.ReadTimeout('simulated response loss')
            return httpx.Response(200,json={'simulated':True,'route':route})
        def factory(**kwargs):return httpx.AsyncClient(transport=httpx.MockTransport(upstream),**kwargs)
        gateway=E1Gateway(c,'http://gpustack.invalid',lambda who,m:'synthetic-inference-key',client_factory=factory)
        fail=[False]
        def infer(token,model,kind='async'):
            return asyncio.run(gateway.infer(token,SimpleNamespace(project='pilot'),model,'r1',
                                            {'model':model,'stream':False},kind))
        session=c.begin('002','local planned maintenance')
        for i in range(40):infer('planned-'+str(i),'model1' if i%2 else 'model2','sync' if i%3 else 'async')
        assert len(sends)==40 and all(x.endswith('001') for x in sends)
        scenarios.append({'name':'计划维护时两模型连续请求','passed':True,'completed':40,'target_new_sends':0})
        def evidence(session,**kw):return dict(worker='002',session=session,observed_at=100.,**kw)
        def stop(worker,session):
            node_actions.append({'worker':worker,'action':'stop'})
            return evidence(session,worker_stopped=True,instances_stopped=True,gpu_idle=True)
        c.stop('002',session,evidence(session,bypass_closed=True,scheduling_blocked=True,route_withdrawn=True),stop)
        def restore(worker,session):node_actions.append({'worker':worker,'action':'restore'})
        def probe(r,s):return evidence(s,ok=True,**{k:r[k] for k in ('route','revision','image_id','instance_id')})
        c.recover('002',session,restore,probe)
        assert node_actions==[{'worker':'002','action':'stop'},{'worker':'002','action':'restore'}]
        scenarios.append({'name':'只停止目标及探针恢复','passed':True,'node_actions':list(node_actions)})
        c.reserve('hold001','pilot','model1','r1','async',120.)
        fail[0]=True
        try:infer('lost002','model1')
        except ServiceError as e:assert e.code=='UNKNOWN_BACKEND_ACTIVITY'
        else:raise AssertionError('Lost response must not succeed')
        c.finish('hold001',True)
        before=len(sends)
        for i in range(20):infer('fault-'+str(i),'model1' if i%2 else 'model2')
        assert all(x.endswith('001') for x in sends[before:])
        session=c.begin('002','local unknown investigation')
        assert not c.drained('002',session)
        scenarios.append({'name':'002 UNKNOWN局部隔离','passed':True,'healthy_completed':20,'drained':False})
        c.resolve('lost002','SIMULATED explicit GPU process termination evidence')
        fail[0]=False;c.recover('002',session,restore,probe)
        c.isolate('002','local worker fault')
        c.isolate('001','local model1 instance fault',model='model1')
        try:infer('unavailable','model1')
        except ServiceError as e:assert e.code=='E1_MODEL_UNAVAILABLE'
        else:raise AssertionError('Model1 must be unavailable')
        infer('model2-continues','model2')
        scenarios.append({'name':'模型1无副本时模型2继续','passed':True,'model1_unavailable':True,'model2_completed':1})
        state=c.snapshot()
        assert len([x for x in state['requests'].values() if x['token']=='lost002'])==1
        return {'scope':'LOCAL_SIMULATION_ONLY','real_gpustack_tested':False,'servers_modified':False,
            'p4_runtime_integrated':False,'http_sends':len(sends),'scenarios':scenarios,
            'all_passed':all(x['passed'] for x in scenarios),
            'final_worker_modes':{w:v['mode'] for w,v in state['workers'].items()},
            'request_states':{k:sum(r['state']==k for r in state['requests'].values())
                              for k in ('DISPATCHING','UNKNOWN','COMPLETED','RESOLVED')}}


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--output',type=Path,default=Path('results/maas-e1-local'))
    args=parser.parse_args();result=run();args.output.mkdir(parents=True,exist_ok=True)
    (args.output/'acceptance.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps(result,ensure_ascii=False))


if __name__=='__main__':main()
