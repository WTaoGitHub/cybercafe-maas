"""Synthetic end-to-end benchmark. Run on controller with 1 or 2 replicas."""
import json
from pathlib import Path
import sys
import time
from gpustack_admin import api
from verify_ocr import run, encoded, base, opener

replicas = int(sys.argv[1])
model = api('/v2/models/1')
model['replicas'] = replicas
api('/v2/models/1', model, 'PUT')
for _ in range(60):
    instances = api('/v2/models/1/instances')['items']
    if len(instances) == replicas and all(i['state']=='running' for i in instances):
        break
    time.sleep(1)
else:
    raise RuntimeError('replicas did not become ready')
body = {'model':'rapidocr-gpu','messages':[{'role':'user','content':[
    {'type':'image_url','image_url':{'url':'data:image/png;base64,'+encoded}}]}]}
report = run(base+'/v1/chat/completions', body, 32, 4, opener)
report['replicas'] = replicas
path = Path(f'/root/wtaobj/gpu-gpustack-{replicas}-replicas.json')
path.write_text(json.dumps(report,indent=2))
print(path.read_text())
