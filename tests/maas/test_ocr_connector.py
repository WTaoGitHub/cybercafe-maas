import json,time
from types import SimpleNamespace
from datetime import datetime,timedelta,timezone
import httpx,pytest
from maas_platform.ocr_connector import OCRAdapter,preflight
from maas_platform.versioning import register_pin
from maas_platform.errors import ServiceError


def target(tmp_path):
    route={'id':1,'name':'ocr-p2','access_policy':'authed'}
    targets=[{'model_id':2,'weight':100}]
    model={'id':2,'backend':'ocr-custom','backend_version':'p2'}
    backend={'backend_name':'ocr-custom','version_configs':{'p2':{'image_name':'local/ocr:p2'}}}
    pin=register_pin('p2',route,targets,model,backend,'sha256:'+'a'*64,['001','002'])
    snapshot={'observed_at':time.time(),'version':{'version':'v2.2.3'},'route':route,'targets':targets,
              'model':model,'backend':backend,'gateway_max_attempts':3,
              'inventory':{w:{'observed_at':time.time(),'image_name':pin.image_name,'image_id':pin.image_id} for w in pin.worker_names}}
    p=tmp_path/'pin';p.write_text(json.dumps(pin.public()))
    f=tmp_path/'preflight';f.write_text(json.dumps(snapshot))
    return {'revision':'p2','upstream_model':'ocr-p2','pin_file':str(p),'preflight_file':str(f)},snapshot


@pytest.mark.parametrize('change',['stale','image','model','gateway'])
def test_preflight_fails_closed(tmp_path,change):
    model,snap=target(tmp_path);assert preflight(model)
    if change=='stale':snap['observed_at']-=31
    if change=='image':snap['inventory']['001']['image_id']='sha256:'+'b'*64
    if change=='model':snap['model']['backend_version']='different'
    if change=='gateway':snap['gateway_max_attempts']=0
    from pathlib import Path
    Path(model['preflight_file']).write_text(json.dumps(snap))
    with pytest.raises(ServiceError):preflight(model)


@pytest.mark.parametrize('case',['success','timeout','503','malformed','oversize','denied'])
def test_ocr_sends_once_and_only_proven_completion_releases_activity(tmp_path,monkeypatch,case):
    model,_=target(tmp_path);events=[];requests=[]
    client_class=httpx.AsyncClient
    def transport(request):
        requests.append(request)
        if case=='timeout':raise httpx.ReadTimeout('secret must not leak',request=request)
        if case=='503':return httpx.Response(503,json={'error':'unavailable'})
        if case=='denied':return httpx.Response(403,json={'error':'denied'})
        if case=='oversize':return httpx.Response(200,content=b'x'*2000)
        value={'text':'hello','lines':[],'elapsed_ms':1,'node':'001'}
        return httpx.Response(200,json={'choices':[{'message':{'content':'invalid' if case=='malformed' else json.dumps(value)}}]})
    monkeypatch.setattr('maas_platform.ocr_connector.httpx.AsyncClient',lambda **kw:client_class(transport=httpx.MockTransport(transport),**kw))
    task={'deadline':datetime.now(timezone.utc)+timedelta(seconds=10)}
    service=SimpleNamespace(settings=SimpleNamespace(gpustack_url='http://gateway.test',max_output_bytes=1000),
        auth=SimpleNamespace(key_for=lambda _: 'registered-key'),begin_backend=lambda _:events.append('begin'),
        complete_backend=lambda _:events.append('complete'))
    ctx=SimpleNamespace(task=task,model=model,identity=object(),service=service,deadline=time.monotonic()+10,
                        read_input=lambda:(b'fixture','image/png'),check=lambda:None)
    if case=='success':assert OCRAdapter().execute(ctx)['text']=='hello'
    else:
        with pytest.raises(ServiceError) as exc:OCRAdapter().execute(ctx)
        assert 'secret' not in str(exc.value)
    assert len(requests)==1
    assert events==(['begin','complete'] if case in ('success','malformed','denied') else ['begin'])
    assert requests[0].headers['authorization']=='Bearer registered-key'
    body=json.loads(requests[0].content)
    assert body['model']=='ocr-p2' and body['stream'] is False


def test_slow_trickling_response_obeys_absolute_deadline(monkeypatch):
    import asyncio
    closed=[]
    class Trickling(httpx.AsyncByteStream):
        async def __aiter__(self):
            while True:
                await asyncio.sleep(.01)
                yield b' '
        async def aclose(self):closed.append(True)
    original=httpx.AsyncClient
    monkeypatch.setattr('maas_platform.ocr_connector.httpx.AsyncClient',lambda **kw:original(
        transport=httpx.MockTransport(lambda request:httpx.Response(200,stream=Trickling())),**kw))
    ctx=SimpleNamespace(deadline=time.monotonic()+.1,service=SimpleNamespace(
        settings=SimpleNamespace(gpustack_url='http://gateway.test',max_output_bytes=10000),
        complete_backend=lambda _:pytest.fail('Incomplete stream cannot prove completion')))
    started=time.monotonic()
    with pytest.raises(ServiceError) as exc:asyncio.run(OCRAdapter()._infer(ctx,'key',{},.1))
    assert exc.value.code=='UNKNOWN_BACKEND_ACTIVITY'
    assert time.monotonic()-started<1 and closed
