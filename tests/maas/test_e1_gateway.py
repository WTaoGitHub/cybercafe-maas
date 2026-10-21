import asyncio
from dataclasses import replace
import json
from types import SimpleNamespace
import httpx
import pytest
from maas_platform.e1.gateway import E1Gateway
from maas_platform.errors import ServiceError
from test_e1_control import env, reserve


def run(c, handler, calls=None, authorize=None, limit=1024):
    def factory(**kw):
        assert kw['trust_env'] is False and kw['follow_redirects'] is False
        return httpx.AsyncClient(transport=httpx.MockTransport(handler),**kw)
    return E1Gateway(c,'http://gpustack.test',authorize or (lambda who,model:'scoped-test-key'),
                     max_output_bytes=limit,client_factory=factory)


def request(g, token='one',model='m1',project='p',kind='async'):
    return g.infer(token,SimpleNamespace(project=project),model,'r1',{'model':model,'stream':False},kind)


def test_transport_uses_only_reserved_alias_and_auth_not_customer_routing(env):
    c,*_=env;seen=[]
    def upstream(r):
        seen.append(r)
        assert json.loads(r.content)['model']=='m1-001'
        assert r.headers['authorization']=='Bearer scoped-test-key'
        assert 'X-GPUStack-Model-Instance' not in r.headers
        return httpx.Response(200,json={'ok':True})
    g=run(c,upstream)
    assert asyncio.run(request(g))=={'ok':True}
    assert len(seen)==1 and c.snapshot()['requests']['one']['state']=='COMPLETED'
    with pytest.raises(ServiceError):asyncio.run(request(g))
    assert len(seen)==1


def test_timeout_quarantines_only_actual_worker_and_other_model_keeps_serving(env):
    c,*_=env
    # Select 002 for the uncertain request while 001 has an in-flight reservation.
    reserve(c,'on001')
    seen=[]
    def upstream(r):
        alias=json.loads(r.content)['model'];seen.append(alias)
        if alias=='m1-002':raise httpx.ReadTimeout('lost response')
        return httpx.Response(200,json={'ok':True})
    g=run(c,upstream)
    with pytest.raises(ServiceError,match='unconfirmed'):asyncio.run(request(g,'uncertain'))
    c.finish('on001',True)
    assert c.snapshot()['requests']['uncertain']['state']=='UNKNOWN'
    assert asyncio.run(request(g,'healthy','m2'))=={'ok':True}
    assert seen==['m1-002','m2-001']


@pytest.mark.parametrize('case',['500','oversize','encoding','invalid-json','401'])
def test_response_classification_and_no_retries(env,case):
    c,*_=env;seen=[]
    def upstream(r):
        seen.append(r)
        if case=='500':return httpx.Response(500)
        if case=='oversize':return httpx.Response(200,content=b'x'*1025)
        if case=='encoding':return httpx.Response(200,content=b'{}',headers={'content-encoding':'br'})
        if case=='invalid-json':return httpx.Response(200,content=b'no json')
        return httpx.Response(401)
    with pytest.raises(ServiceError):asyncio.run(request(run(c,upstream)))
    state=c.snapshot()['requests']['one']['state']
    assert state==('COMPLETED' if case in ('401','invalid-json') else 'UNKNOWN')
    assert len(seen)==1


def test_auth_failure_never_reserves_or_sends(env):
    c,*_=env
    def deny(who,model):raise ServiceError('FORBIDDEN','wrong project',403)
    g=run(c,lambda r:pytest.fail('must not send'),authorize=deny)
    with pytest.raises(ServiceError):asyncio.run(request(g))
    assert c.snapshot()['requests']=={}


def test_cancelled_client_leaves_durable_unknown(env):
    c,*_=env
    async def upstream(r):raise asyncio.CancelledError()
    with pytest.raises(asyncio.CancelledError):asyncio.run(request(run(c,upstream)))
    assert c.snapshot()['requests']['one']['state']=='UNKNOWN'


def test_mixed_sync_async_burst_during_target_maintenance(env):
    c,*_=env;c.begin('002','planned')
    seen=[]
    def upstream(r):
        seen.append(json.loads(r.content)['model'])
        return httpx.Response(200,json={'ok':True})
    g=run(c,upstream)
    async def flow():
        # Both logical models continue; no target route gets a new send.
        for i in range(40):
            await request(g,str(i),'m1' if i%2 else 'm2',kind='sync' if i%3 else 'async')
    asyncio.run(flow())
    assert len(seen)==40 and all(alias.endswith('001') for alias in seen)
    assert all(r['state']=='COMPLETED' for r in c.snapshot()['requests'].values())
