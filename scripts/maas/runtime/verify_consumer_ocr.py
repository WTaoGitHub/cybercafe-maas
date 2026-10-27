"""One-shot consumer contract acceptance through private TLS. Never replay a send."""
import base64,io,json,os,ssl,subprocess,time,urllib.request,urllib.error
from pathlib import Path
ROOT=Path('/root/wtaobj/maas-consumer-ocr-r1')
LIVE=Path('/root/wtaobj/maas-live')
OUT=ROOT/'acceptance.json'

def main():
 assert not OUT.exists()
 key=(LIVE/'secrets/pilot-key').read_text().strip()
 ctx=ssl.create_default_context(cafile='/root/wtaobj/maas-private-tls/server.crt')
 opener=urllib.request.build_opener(urllib.request.ProxyHandler({}),urllib.request.HTTPSHandler(context=ctx))
 report={'state':'STARTED','tls_verified':True,'cases':{},'started_at':time.time(),'automatic_inference_replays':False}
 def save():
  with OUT.open('w') as f:os.fchmod(f.fileno(),0o600);json.dump(report,f,indent=2);f.flush();os.fsync(f.fileno())
 def request(path,body,authorized=True):
  headers={'Content-Type':'application/json'}
  if authorized:headers['Authorization']='Bearer '+key
  req=urllib.request.Request('https://127.0.0.1:18832'+path,data=json.dumps(body).encode(),headers=headers)
  try:
   with opener.open(req,timeout=50) as r:return r.status,json.load(r),r.headers.get('X-Request-ID')
  except urllib.error.HTTPError as e:return e.code,json.load(e),e.headers.get('X-Request-ID')
 def usage():
  code="import os,json,psycopg; c=psycopg.connect(os.environ['MAAS_DATABASE_URL']);r=c.execute(\"SELECT count(*) FROM sync_usage_events\").fetchone();print(json.dumps({'sync_success_events':r[0]}));c.close()"
  return json.loads(subprocess.check_output(['docker','exec','maas-tasks-api-1','python','-c',code],text=True))
 save()
 try:
  report['before']=usage();save()
  raw=(LIVE/'probes/upright.png').read_bytes();encoded=base64.b64encode(raw).decode()
  value={'model':'ocr.default','image_base64':encoded,'language':'zh','return_boxes':True,'request_id':'consumer-live-base64-r1'}
  code,data,rid=request('/ocr',value);assert code==200 and len(data['lines'])==46 and data.get('choices') is None
  assert all(isinstance(l['text'],str) and 0<=l['confidence']<=1 and len(l['polygon'])==4 for l in data['lines'])
  report['cases']['base64']={'http':code,'lines':len(data['lines']),'request_id':rid,'polygon_valid':True};save()
  value={'model':'ocr.default','image_base64':'data:image/png;base64,'+encoded,'return_boxes':False,'request_id':'consumer-live-data-url-r1'}
  code,data,rid=request('/v1/ocr',value);assert code==200 and len(data['lines'])==46 and all('polygon' not in l for l in data['lines'])
  report['cases']['data_url_alias']={'http':code,'lines':46,'polygon_omitted':True,'request_id':rid};save()
  value={'model':'ocr.default','image_url':'https://www.python.org/static/img/python-logo.png','request_id':'consumer-live-public-url-r1'}
  code,data,rid=request('/ocr',value);assert code==200 and isinstance(data['lines'],list)
  report['cases']['public_url']={'http':code,'lines':len(data['lines']),'request_id':rid,'tls_download_verified':True};save()
  # Generate a valid blank PNG inside the already tested API image; no new host dependency.
  code_png="from PIL import Image;import io,base64;f=io.BytesIO();Image.new('RGB',(64,64),'white').save(f,format='PNG');print(base64.b64encode(f.getvalue()).decode())"
  blank=subprocess.check_output(['docker','exec','maas-tasks-api-1','python','-c',code_png],text=True).strip()
  code,data,rid=request('/ocr',{'model':'ocr.default','image_base64':blank});assert code==200 and data=={'lines':[]}
  report['cases']['blank']={'http':code,'lines':0,'request_id':rid};save()
  errors={
   'bad_base64':{'model':'ocr.default','image_base64':'invalid!'},
   'private_url':{'model':'ocr.default','image_url':'http://169.254.169.254/latest/meta-data/'},
   'two_sources':{'model':'ocr.default','image_base64':encoded,'image_url':'https://example.org/a.png'},
   'unsupported_language':{'model':'ocr.default','image_base64':encoded,'language':'unsupported'}}
  for name,value in errors.items():
   code,data,rid=request('/ocr',value);assert code==422
   report['cases'][name]={'http':code,'error_code':data['error']['code'],'request_id':rid};save()
  code,data,rid=request('/ocr',{'model':'ocr.default','image_base64':encoded},authorized=False);assert code==401
  report['cases']['unauthorized']={'http':code,'error_code':data['error']['code']};save()
  report['after']=usage();assert report['after']['sync_success_events']-report['before']['sync_success_events']==4
  report['successful_images']=4;report['success_event_delta']=4;report['state']='PASSED'
 except Exception as e:
  report['state']='FAILED';report['error_type']=type(e).__name__
  raise RuntimeError('Consumer acceptance failed; inspect saved phase, do not replay uncertain inference') from None
 finally:report['finished_at']=time.time();save()
 print(json.dumps(report))

if __name__=='__main__':main()
