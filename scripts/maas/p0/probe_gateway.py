"""Isolated real-gateway generic, retry and metering acceptance. Cleans every resource."""
import base64,datetime,json,time,traceback,urllib.request
from pathlib import Path
from common import Resources,admin,request,prepare_backend,stats
ROOT=Path('/root/wtaobj/maas-p0');res=Resources();out={'checks':{},'production_models_mutated':False,'synthetic_zero_token_fixture':True}
original={k:admin('/v2/models/1').get(k) for k in ['name','backend_version','replicas','generic_proxy']}
try:
 b,m,route,instances=prepare_backend(res);out['probe_ids']={'model':m['id'],'route':route['id'],'backend':b['id']}
 user,op=res.user('gateway');info,key=res.key(op,'gateway',[m['name'],'rapidocr-gpu'])
 today=datetime.datetime.now(datetime.timezone.utc).date(); dates={'start_date':str(today-datetime.timedelta(days=1)),'end_date':str(today+datetime.timedelta(days=1))}
 def usage():
  data=admin('/v2/usage/breakdown',{**dates,'group_by':['route'],'scope':'all','filters':{'users':[{'identity':{'value':{'user_name':user['username']},'current':{'user_id':user['id']}}}]}})
  return {'summary':data['summary'],'items':data['items']}
 def count(data):return sum(x.get('api_requests',0) for x in data['items'])
 baseline=usage();previous=count(baseline);out['usage_baseline']=baseline
 def exercise(label,path,body,expected):
  before=stats(instances,label)
  status,raw,headers=request(path,body,key,headers={'x-maas-probe-case':label})
  observed=stats(instances,label)-before
  record={'status':status,'backend_invocations':observed,'content_type':headers.get('content-type','')}
  out['checks'][label]=record
  assert status==expected,(label,status)
  return raw,record
 body={'model':m['name'],'messages':[{'role':'user','content':'P0 fixture'}]}
 # New model routes/ingress are reconciled asynchronously.
 for _ in range(60):
  code,_,_=request('/v1/chat/completions',{**body,'probe_case':'warmup'},key)
  if code==200:break
  time.sleep(1)
 else:raise RuntimeError('Gateway route not ready')
 time.sleep(12);previous=count(usage())
 cases=[('chat_usage','/v1/chat/completions',body,200),('chat_no_usage','/v1/chat/completions',{**body,'probe_no_usage':True},200),('chat_sse','/v1/chat/completions',{**body,'stream':True},200),('native_generic','/model/proxy/'+str(route['id'])+'/native/echo',{'hello':'native payload'},200),('post_503','/v1/chat/completions',{**body,'probe_fail':'always_503'},503)]
 for label,path,payload,expected in cases:
  raw,record=exercise(label,path,payload,expected)
  if label=='native_generic':
   value=json.loads(raw);assert value['native'] and value['path']=='/native/echo';assert value['payload']['hello']=='native payload'
  if label=='chat_sse':assert b'[DONE]' in raw
  time.sleep(12);data=usage();record['usage_api_requests_delta']=count(data)-previous;record['usage_snapshot']=data;previous=count(data)
  print(label,{k:v for k,v in record.items() if k!='usage_snapshot'},flush=True)
 # Authentication must apply on the generic ingress too.
 code,_,_=request('/model/proxy/'+str(route['id'])+'/native/echo',{'hello':'denied'})
 out['checks']['generic_unauthenticated']={'status':code};assert code in (401,403)
 # Existing real OCR: no image/text saved in evidence, no fake token fields.
 image=Path('/root/wtaobj/service-validation/fixtures/upright.png')
 payload={'model':'rapidocr-gpu','messages':[{'role':'user','content':[{'type':'image_url','image_url':{'url':'data:image/png;base64,'+base64.b64encode(image.read_bytes()).decode()}}]}]}
 for stream in (False,True):
  label='ocr_sse' if stream else 'ocr_json'
  status,raw,headers=request('/v1/chat/completions',{**payload,'stream':stream},key)
  assert status==200,(label,status)
  if stream:assert b'[DONE]' in raw
  else:assert json.loads(json.loads(raw)['choices'][0]['message']['content'])['lines']
  time.sleep(12);data=usage();out['checks'][label]={'status':status,'usage_api_requests_delta':count(data)-previous,'usage_snapshot':data};previous=count(data)
 out['ok']=True
except Exception as exc:
 out['ok']=False;out['failure']={'type':type(exc).__name__,'message':str(exc)};raise
finally:
 out['cleanup_ok']=res.cleanup();out['cleanup_failures']=res.failures
 current={k:admin('/v2/models/1').get(k) for k in original};out['original_model_unchanged']=current==original
 (ROOT/'gateway-acceptance.json').write_text(json.dumps(out,indent=2)+'\n')
print(json.dumps({'ok':out['ok'],'cleanup_ok':out['cleanup_ok'],'original_model_unchanged':out['original_model_unchanged']}))
