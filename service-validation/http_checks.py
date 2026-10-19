"""Controller functional checks; run only outside timed load phases."""
import base64,hashlib,json,time,urllib.error,urllib.request
from pathlib import Path
ROOT=Path('/root/wtaobj/service-validation');key=(ROOT/'.benchmark-key').read_text().strip()
def call(name,mode,rotation=0,stream=False,missing=False):
 content=[{'type':'text','text':f'ocr_variant={mode};rotation_cw={rotation}'}]
 if not missing:content.append({'type':'image_url','image_url':{'url':'data:image/png;base64,'+base64.b64encode((ROOT/'fixtures'/name).read_bytes()).decode()}})
 body={'model':'wta-ocr-validation','stream':stream,'stream_options':{'include_usage':True},'ocr_variant':mode,'rotation_cw':rotation,'messages':[{'role':'user','content':content}]}
 req=urllib.request.Request('http://127.0.0.1:18080/v1/chat/completions',data=json.dumps(body).encode(),headers={'Authorization':'Bearer '+key,'Content-Type':'application/json'})
 with urllib.request.urlopen(req,timeout=120) as response:
  data=response.read().decode();ctype=response.headers.get('Content-Type','')
 if stream:return data,ctype
 return json.loads(json.loads(data)['choices'][0]['message']['content'])
output={'checked_at':time.time(),'variants':{}}
for mode in ['v4_legacy','v4_upright','v4_guarded','v6_small']:
 result=call('upright.png',mode);assert result['variant']==mode and result['lines'];output['variants'][mode]={'lines':len(result['lines']),'node':result['node']}
for mode in ['v4_upright','v6_small']:
 original=call('upright.png',mode);corrected=call('cw90.png',mode,270);assert original['text']==corrected['text'];assert len(original['lines'])==len(corrected['lines'])
 output[mode+'_explicit_rotation_text_restored']=True
nonstream=call('upright.png','v4_guarded');raw,ctype=call('upright.png','v4_guarded',stream=True);assert 'text/event-stream' in ctype
chunks=[x[6:] for x in raw.splitlines() if x.startswith('data: ')];assert chunks[-1]=='[DONE]';events=[json.loads(x) for x in chunks[:-1]];text=''.join(x['choices'][0]['delta'].get('content','') for x in events);assert text==nonstream['text'];assert events[-1]['choices'][0]['finish_reason']=='stop';output['sse_nonstream_text_equal']=True
try:call('upright.png','v4_guarded',missing=True);raise AssertionError('Expected HTTP400')
except urllib.error.HTTPError as e:assert e.code==400;output['missing_image_http400']=True
(ROOT/'http-checks.json').write_text(json.dumps(output,indent=2)+'\n');print(json.dumps(output))
