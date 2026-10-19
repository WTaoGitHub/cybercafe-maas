"""Run on controller; credentials stay on controller and are never printed."""
import http.cookiejar
import json
import os
from pathlib import Path
import sys
import urllib.parse
import urllib.request

base = 'http://127.0.0.1:18080'
opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
key_path = Path(os.getenv('GPUSTACK_API_KEY_FILE', '/root/wtaobj/.gpustack-api-key'))
api_key = os.getenv('GPUSTACK_API_KEY') or (key_path.read_text().strip() if key_path.exists() else '')
if api_key:
    opener.addheaders = [('Authorization', 'Bearer '+api_key)]
else:
    password_path = Path(os.getenv('GPUSTACK_PASSWORD_FILE', '/root/wtaobj/gpustack-server/initial_admin_password'))
    if not password_path.exists():
        raise RuntimeError('GPUStack credentials unavailable: supply GPUSTACK_API_KEY_FILE or GPUSTACK_PASSWORD_FILE')
    password = password_path.read_text().strip()
    opener.open(urllib.request.Request(base+'/auth/login', data=urllib.parse.urlencode({'username':'admin','password':password}).encode()))

def api(path, data=None, method=None):
    req = urllib.request.Request(base+path, data=json.dumps(data).encode() if data is not None else None,
                                 headers={'Content-Type':'application/json'}, method=method)
    try:
        with opener.open(req) as response:
            raw = response.read()
            return json.loads(raw) if raw else None
    except urllib.error.HTTPError as exc:
        print(exc.code, exc.read().decode(), file=sys.stderr)
        raise

if __name__ == '__main__':
    if sys.argv[1] == 'cluster':
        clusters = api('/v2/clusters')['items']
        cluster = next((c for c in clusters if c['name']=='cybercafe-poc'), None)
        if cluster is None:
            cluster = api('/v2/clusters', {'name':'cybercafe-poc','provider':'Docker','server_url':'http://10.11.31.1:18080','system_default_container_registry':'quay.io'})
        token = api(f"/v2/clusters/{cluster['id']}/registration-token")
        dest = Path('/root/wtaobj/worker-token')
        dest.touch(mode=0o600, exist_ok=True)
        dest.write_text(token['token'])
        print('cluster',cluster['id'],cluster['name'],'registration token saved privately')
    elif sys.argv[1] == 'deploy-ocr':
        backend = {'backend_name':'RapidOCR-custom', 'health_check_path':'/health',
                   'default_version':'0.1', 'version_configs':{'0.1':{
                       'image_name':'quay.io/cybercafe-gpu-ocr:0.1', 'custom_framework':'cuda',
                       'entrypoint':'python3 /app/server.py',
                       'run_command':'--port {{port}} --model {{model_name}}'}}}
        existing = api('/v2/inference-backends')['items']
        if not any(b['backend_name']=='RapidOCR-custom' for b in existing):
            api('/v2/inference-backends', backend)
        models = api('/v2/models')['items']
        if not any(m['name']=='rapidocr-gpu' for m in models):
            result = api('/v2/models', {'name':'rapidocr-gpu','source':'local_path',
                'local_path':'/root/wtaobj/gpu-ocr','cluster_id':1,'replicas':2,
                'categories':['llm'],'placement_strategy':'spread',
                'backend':'RapidOCR-custom','backend_version':'0.1',
                'cpu_offloading':False,'distributed_inference_across_workers':False,
                'env':{'GPUSTACK_MODEL_VRAM_CLAIM':str(2*1024**3)}})
            print('OCR model created', result['id'])
        else:
            print('OCR deployment already exists')
        models = api('/v2/models')['items']
        model = next(m for m in models if m['name']=='rapidocr-gpu')
        routes = api('/v2/model-routes')['items']
        if not any(r['name']=='rapidocr-gpu' for r in routes):
            route = api('/v2/model-routes', {'name':'rapidocr-gpu','categories':['llm'],
                        'targets':[{'model_id':model['id'],'weight':100}]})
            print('OCR route created', route['id'])
    else:
        print(json.dumps(api(sys.argv[1]), ensure_ascii=False)[:15000])
