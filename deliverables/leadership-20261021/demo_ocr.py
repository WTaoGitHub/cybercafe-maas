"""Manual single-image demo. Default is offline validation; --run sends requests."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import ssl
import time
import urllib.error
import urllib.request
import uuid


def save(path, value):
    data=json.dumps(value,ensure_ascii=False,indent=2).encode()
    temp=path.with_suffix('.tmp')
    fd=os.open(temp,os.O_CREAT|os.O_WRONLY|os.O_TRUNC,0o600)
    with os.fdopen(fd,'wb') as f:
        os.fchmod(f.fileno(),0o600);f.write(data);f.flush();os.fsync(f.fileno())
    os.replace(temp,path)


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--image',type=Path,required=True)
    p.add_argument('--base-url',default='https://127.0.0.1:18832')
    p.add_argument('--key-file',type=Path)
    p.add_argument('--ca-file',type=Path)
    p.add_argument('--output',type=Path,default=Path('.maas/leadership-demo'))
    p.add_argument('--wait-seconds',type=int,default=40)
    p.add_argument('--run',action='store_true')
    a=p.parse_args()
    data=a.image.read_bytes()
    media='image/png' if data.startswith(b'\x89PNG\r\n\x1a\n') else ('image/jpeg' if data.startswith(b'\xff\xd8\xff') else None)
    if not media or not 0<len(data)<=10*1024*1024:raise ValueError('Use a bounded PNG/JPEG')
    if not a.run:
        print('OFFLINE CHECK OK: image accepted. No network requests sent. Add --run only in an approved demo window.')
        return
    if not a.key_file or not a.ca_file:p.error('--run requires --key-file and --ca-file')
    if a.key_file.stat().st_mode & 0o077:raise ValueError('Key file must have mode 600 or stricter')
    if not a.base_url.startswith('https://') or not 1<=a.wait_seconds<=300:raise ValueError('Use verified HTTPS and a bounded wait')
    context=ssl.create_default_context(cafile=str(a.ca_file))
    key=a.key_file.read_text().strip()
    a.output.mkdir(parents=True,exist_ok=True,mode=0o700)
    manifest=a.output/'request.json';digest=hashlib.sha256(data).hexdigest()
    def request(path,body=None,media='application/json',idem=None):
        raw=json.dumps(body).encode() if isinstance(body,dict) else body
        headers={'Authorization':'Bearer '+key,'Content-Type':media}
        if idem:headers['Idempotency-Key']=idem
        req=urllib.request.Request(a.base_url.rstrip('/')+path,data=raw,headers=headers)
        # Explicit opener ignores proxy environment and refuses redirects.
        class NoRedirect(urllib.request.HTTPRedirectHandler):
            def redirect_request(self,*args,**kwargs):return None
        opener=urllib.request.build_opener(urllib.request.ProxyHandler({}),NoRedirect(),urllib.request.HTTPSHandler(context=context))
        with opener.open(req,timeout=40) as r:return r.status,json.load(r)
    if manifest.exists():
        job=json.loads(manifest.read_text())
        if job['image_sha256']!=digest or job['base_url']!=a.base_url:raise ValueError('Output directory belongs to another request')
    else:
        status,art=request('/maas/v1/artifacts',data,media)
        assert status==201
        print('1. Uploaded image:',art['id'])
        job={'image_sha256':digest,'base_url':a.base_url,'key':'demo-'+uuid.uuid4().hex,
             'body':{'model':'ocr.default','input':{'image':{'artifact_id':art['id']}},
                     'parameters':{},'timeout_seconds':300,'max_attempts':1}}
        save(manifest,job)
    status,task=request('/maas/v1/tasks',job['body'],idem=job['key'])
    job['task_id']=task['id'];save(manifest,job)
    print('2. Task:',task['id'],'HTTP',status)
    deadline=time.monotonic()+a.wait_seconds;last=None
    while True:
        if last!=task['state']:print('3. State:',task['state']);last=task['state']
        if task['state'] in ('SUCCEEDED','FAILED','CANCELLED'):break
        if time.monotonic()>deadline:
            print('Local wait ended. Request saved; rerun with SAME output directory to query the same task.');return
        time.sleep(1);_,task=request('/maas/v1/tasks/'+task['id'])
    save(a.output/'task.json',task)
    if task['state']!='SUCCEEDED':
        print('Task did not succeed:',task.get('error_code'),'Do not resend with a new key blindly.');return
    _,result=request('/maas/v1/artifacts/'+task['result_artifact_id'])
    save(a.output/'result.json',result)
    print('4. Result saved:',a.output/'result.json')
    print('Lines:',len(result['outputs']['lines']))
    print('Text preview:',result['outputs']['text'][:180])


if __name__=='__main__':
    try:main()
    except urllib.error.HTTPError as e:
        print('HTTP error:',e.code,'Request-ID:',e.headers.get('X-Request-ID','unknown'))
        raise SystemExit(1)
    except Exception as e:
        print('Demo stopped:',type(e).__name__,'Inspect access/configuration privately. No automatic inference retry.')
        raise SystemExit(1)
