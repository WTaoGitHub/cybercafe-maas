import asyncio,base64,io,json,socket
from types import SimpleNamespace
import httpcore,httpx,pytest
from PIL import Image
from pydantic import ValidationError
from maas_platform.consumer_ocr import ConsumerOCRRequest,decode_base64,validate_image,consumer_output,ConsumerOCR
from maas_platform.image_fetch import image_url,PublicNetworkBackend,PublicImageFetcher,PublicImageTransport
from maas_platform.errors import ServiceError
from maas_platform.sync_ingress import SyncIngress


def png():
    f=io.BytesIO();Image.new('RGB',(4,3),'white').save(f,format='PNG');return f.getvalue()


def response(lines):return json.dumps({'choices':[{'message':{'content':json.dumps({'lines':lines})}}]}).encode()


@pytest.mark.parametrize('change',[{}, {'image_base64':'x','image_url':'https://example.org/a.png'},
 {'image_base64':'x','return_boxes':'false'},{'image_base64':'x','language':'fr'},
 {'image_base64':'x','request_id':'a\nb'},{'image_base64':'x','unknown':1}])
def test_request_requires_one_source_and_strict_supported_options(change):
    with pytest.raises(ValidationError):ConsumerOCRRequest(model='ocr.default',**change)


def test_base64_data_url_and_real_image_validation():
    raw=png();encoded=base64.b64encode(raw).decode()
    for value in [encoded,'data:image/png;base64,'+encoded]:
        actual,media=decode_base64(value,10000);assert actual==raw and validate_image(actual,media)=='image/png'
    with pytest.raises(ServiceError):decode_base64('not base64!',10000)
    with pytest.raises(ServiceError):decode_base64(encoded,2)
    with pytest.raises(ServiceError):validate_image(b'not an image')
    with pytest.raises(ServiceError):validate_image(raw,'image/jpeg')


def test_pixel_and_truncated_image_rejected_without_inference(monkeypatch):
    monkeypatch.setattr('maas_platform.consumer_ocr.MAX_PIXELS',2)
    with pytest.raises(ServiceError,match='20 million'):validate_image(png())
    monkeypatch.setattr('maas_platform.consumer_ocr.MAX_PIXELS',20_000_000)
    with pytest.raises(ServiceError):validate_image(png()[:24])


def test_response_mapping_empty_result_optional_fields_and_no_box_request():
    box=[[0,0],[2,0],[2,2],[0,2]]
    assert json.loads(consumer_output(response([{'text':'中文 abc','score':.9,'box':box}])))=={'lines':[{'text':'中文 abc','confidence':.9,'polygon':box}]}
    assert json.loads(consumer_output(response([])))=={'lines':[]}
    assert json.loads(consumer_output(response([{'text':'retained'}])))=={'lines':[{'text':'retained'}]}
    assert json.loads(consumer_output(response([{'text':'x','box':box}]),False))=={'lines':[{'text':'x'}]}

@pytest.mark.parametrize('raw',[b'{}',response(None),response([{'text':'x','score':2}]),response([{'text':'x','score':True}]),response([{'text':'x','box':[[0,0]]}]),response([{'text':42}])])
def test_invalid_output_fails_closed(raw):
    with pytest.raises(ServiceError) as e:consumer_output(raw)
    assert e.value.status==502

@pytest.mark.parametrize('url',['file:///etc/passwd','http://127.0.0.1/a','http://169.254.169.254/a',
 'https://user:pass@example.org/a','https://example.org:8443/a','http://[::1]/a','http://224.0.0.1/a',
 'http://[::ffff:127.0.0.1]/a','http://[2002:7f00:1::]/a'])
def test_forbidden_url_syntax_and_ip_literals(url):
    with pytest.raises(ServiceError):image_url(url)


def test_dns_answers_are_all_checked_and_numeric_ip_is_used_once():
    calls=[]
    class Delegate:
        async def connect_tcp(self,*args,**kw):calls.append((args,kw));return 'stream'
    async def resolver(host,port,**kw):return [(socket.AF_INET,socket.SOCK_STREAM,6,'',('93.184.216.34',port))]
    backend=PublicNetworkBackend(Delegate(),resolver)
    assert asyncio.run(backend.connect_tcp('example.org',443,timeout=3))=='stream'
    assert calls[0][0]==('93.184.216.34',443) and len(calls)==1
    async def mixed(host,port,**kw):return await resolver(host,port,**kw)+[(socket.AF_INET,socket.SOCK_STREAM,6,'',('10.11.31.1',port))]
    calls.clear()
    with pytest.raises(ServiceError):asyncio.run(PublicNetworkBackend(Delegate(),mixed).connect_tcp('example.org',443))
    assert not calls


def test_transport_preserves_original_tls_hostname_while_connecting_to_pinned_ip():
    events=[]
    class Stream(httpcore.AsyncNetworkStream):
        async def read(self,max_bytes,timeout=None):return b'HTTP/1.1 200 OK\r\nContent-Length: 0\r\nConnection: close\r\n\r\n'
        async def write(self,buffer,timeout=None):events.append(('write',buffer))
        async def aclose(self):pass
        async def start_tls(self,ssl_context,server_hostname=None,timeout=None):events.append(('tls',server_hostname,ssl_context.check_hostname));return self
        def get_extra_info(self,info):return None
    class Delegate:
        async def connect_tcp(self,host,port,**kw):events.append(('tcp',host));return Stream()
    async def resolve(host,port,**kw):return [(socket.AF_INET,socket.SOCK_STREAM,6,'',('93.184.216.34',port))]
    async def request():
        transport=PublicImageTransport();transport._pool._network_backend=PublicNetworkBackend(Delegate(),resolve)
        async with httpx.AsyncClient(transport=transport,trust_env=False) as client:assert (await client.get('https://example.org/a.png')).status_code==200
    asyncio.run(request())
    assert ('tcp','93.184.216.34') in events
    assert ('tls','example.org',True) in events
    assert any(b'Host: example.org' in x[1] for x in events if x[0]=='write')

@pytest.mark.parametrize('kind',['ok','redirect','large','gzip','status'])
def test_url_download_limits_no_redirect_no_credentials(kind):
    calls=[]
    def handler(req):
        calls.append(req);assert 'authorization' not in req.headers and 'cookie' not in req.headers
        if kind=='redirect':return httpx.Response(302,headers={'location':'http://127.0.0.1/'})
        if kind=='large':return httpx.Response(200,stream=httpx.ByteStream(b'x'*30))
        if kind=='gzip':return httpx.Response(200,headers={'content-encoding':'gzip'},stream=httpx.ByteStream(b'bad'))
        if kind=='status':return httpx.Response(404)
        return httpx.Response(200,stream=httpx.ByteStream(b'png-data'))
    fetch=PublicImageFetcher(lambda **kw:httpx.AsyncClient(transport=httpx.MockTransport(handler),**kw))
    if kind=='ok':assert asyncio.run(fetch.fetch('https://example.org/a.png',20))==b'png-data'
    else:
        with pytest.raises(ServiceError):asyncio.run(fetch.fetch('https://example.org/a.png',20))
    assert len(calls)==1


class FakeService:
    settings=SimpleNamespace(max_input_bytes=10000,max_output_bytes=10000,gpustack_url='http://gpustack.test')
    auth=SimpleNamespace(authorize=lambda *a:None,key_for=lambda *a:'backend-key')
    def model(self,name):return {'driver':'gpustack.ocr','max_runtime_s':5,'revision':'test-r1','upstream_model':'real-ocr'}

class Ingress(SyncIngress):
    def __init__(self,handler):
        super().__init__(FakeService(),lambda **kw:httpx.AsyncClient(transport=httpx.MockTransport(handler),**kw));self.finishes=[]
    def begin(self,*a):return 'token'
    def finish(self,*args,**kwargs):self.finishes.append(args)


def test_mapping_precedes_success_accounting_and_preserves_chat_compatibility():
    for mapped in [False,True]:
        ingress=Ingress(lambda r:httpx.Response(200,json={'choices':[]}))
        if mapped:
            with pytest.raises(ServiceError):asyncio.run(ingress.infer(None,{'model':'ocr.default'},map_output=consumer_output))
            assert ingress.finishes==[('token',True,False)]
        else:
            assert asyncio.run(ingress.infer(None,{'model':'ocr.default'})).status_code==200
            assert ingress.finishes==[('token',True,True)]


def test_failed_input_does_not_send_or_quarantine_and_transport_unknown_does():
    calls=[]
    def request(r):calls.append(r);raise httpx.ReadError('lost response')
    ingress=Ingress(request)
    async def invalid():raise ServiceError('IMAGE_INVALID','bad image',422)
    with pytest.raises(ServiceError):asyncio.run(ingress.infer(None,{'model':'ocr.default'},prepare_input=invalid))
    assert not calls and ingress.finishes[-1]==('token',True,False)
    with pytest.raises(ServiceError):asyncio.run(ingress.infer(None,{'model':'ocr.default'}))
    assert len(calls)==1 and ingress.finishes[-1]==('token',False,False)


def test_full_facade_sends_one_chat_request_and_maps_result():
    calls=[]
    def handler(req):
        calls.append(req);body=json.loads(req.content)
        assert body['model']=='real-ocr' and 'request_id' not in body and 'language' not in body
        return httpx.Response(200,content=response([{'text':'hello','score':.8}]))
    ingress=Ingress(handler)
    value=ConsumerOCRRequest(model='ocr.default',image_base64=base64.b64encode(png()).decode(),request_id='consumer-1',language='en')
    result=asyncio.run(ConsumerOCR(ingress).infer(None,value))
    assert json.loads(result.body)=={'lines':[{'text':'hello','confidence':.8}]}
    assert len(calls)==1 and ingress.finishes==[('token',True,True)]
