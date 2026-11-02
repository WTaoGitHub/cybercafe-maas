import asyncio,base64,io,json
import pytest
from PIL import Image
from pydantic import ValidationError
from maas_platform.ocr_connector import OCRAdapter
from maas_platform.limits import JsonBodyLimit


def data_url(color='white'):
    out=io.BytesIO();Image.new('RGB',(4,3),color).save(out,format='PNG')
    return 'data:image/png;base64,'+base64.b64encode(out.getvalue()).decode()


@pytest.mark.parametrize('image',[{}, {'data_url':'x','artifact_id':'a'}, {'data_url':'x','extra':1}, {'artifact_id':'a','extra':1}, {'url':'https://example.org/a'}])
def test_source_exclusive(image):
    with pytest.raises(ValidationError):OCRAdapter().validate({'image':image},{})


def test_existing_and_inline_schema():
    assert OCRAdapter().validate({'image':{'artifact_id':'art_x'}},{})[2]=='art_x'
    assert OCRAdapter().validate({'image':{'data_url':data_url()}},{})[2] is None
    with pytest.raises(ValidationError):OCRAdapter().validate({'image':{'data_url':data_url()}},{'threshold':.5})


@pytest.mark.parametrize('path,payload,expected',[
 ('/maas/v1/tasks',{'input':{'image':{'data_url':'x'*100}}},200),
 ('/maas/v1/tasks',{'input':{'image':{'artifact_id':'x'*100}}},413),
 ('/maas/v1/tasks',{'input':{'image':{'data_url':'x'*500}}},413),
 ('/maas/v1/tasks',{'input':{'image':None},'padding':'x'*100},413),
 ('/other',{'input':{'image':{'data_url':'x'*100}}},413),
])
def test_path_and_chunked_body_limits(path,payload,expected):
    async def run():
        result=[];raw=json.dumps(payload).encode();chunks=[raw[:25],raw[25:]]
        async def receive():return {'type':'http.request','body':chunks.pop(0),'more_body':bool(chunks)}
        async def send(event):result.append(event)
        async def app(scope,receive,send):
            assert (await receive())['body']==raw
            await send({'type':'http.response.start','status':200,'headers':[]})
        await JsonBodyLimit(app,limit=64,task_limit=300)({'type':'http','method':'POST','path':path},receive,send)
        return result[0]['status']
    assert asyncio.run(run())==expected
