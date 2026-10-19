"""Run on 001 after preloading the immutable 0.2 image on BOTH nodes."""
from gpustack_admin import api
import time

backend = next(b for b in api('/v2/inference-backends')['items'] if b['backend_name']=='RapidOCR-custom')
version = dict(backend['version_configs']['0.1'])
version['image_name'] = 'quay.io/cybercafe-gpu-ocr:0.2'
backend['version_configs']['0.2'] = version
backend['default_version'] = '0.2'
api('/v2/inference-backends/'+str(backend['id']), backend, 'PUT')
model = next(m for m in api('/v2/models')['items'] if m['name']=='rapidocr-gpu')
model['backend_version'] = '0.2'
api('/v2/models/'+str(model['id']), model, 'PUT')
instances_path = '/v2/models/'+str(model['id'])+'/instances'
old_instances = [i for i in api(instances_path)['items'] if i['backend_version'] != '0.2']
for old in old_instances:
    api('/v2/model-instances/'+str(old['id']), method='DELETE')
    print('Replacing old instance on', old['worker_name'], flush=True)
    for _ in range(120):
        current = api(instances_path)['items']
        if len(current) == model['replicas'] and all(i['state']=='running' for i in current):
            break
        time.sleep(1)
    else:
        raise RuntimeError('Replacement failed to become ready; stopped rolling upgrade')
print('OCR backend upgraded to 0.2; existing 0.1 images retained for rollback')
