"""Sample gateway availability while an operator stops/restarts worker 002."""
import json
from pathlib import Path
import time
from gpustack_admin import api, opener, base
from verify_ocr import request, encoded

body = {'model':'rapidocr-gpu','messages':[{'role':'user','content':[
    {'type':'image_url','image_url':{'url':'data:image/png;base64,'+encoded}}]}]}
start = time.monotonic()
records = []
for _ in range(60):
    record = {'elapsed_seconds':round(time.monotonic()-start,2)}
    try:
        response = request(base+'/v1/chat/completions',body,opener)
        result = json.loads(response['choices'][0]['message']['content'])
        record.update(ok=True,node=result['node'])
    except Exception as exc:
        record.update(ok=False,error=str(exc))
    if len(records)%10 == 0:
        record['workers'] = {w['name']:w['state'] for w in api('/v2/workers')['items']}
    records.append(record)
    print(json.dumps(record),flush=True)
    time.sleep(1)
Path('/root/wtaobj/availability.json').write_text(json.dumps(records,indent=2))
