#!/usr/bin/env python3
"""Submit a local image to GPUStack OCR. Stdlib only; credentials are never saved."""
import argparse,base64,getpass,http.cookiejar,json,mimetypes,os,time,urllib.error,urllib.parse,urllib.request
from pathlib import Path

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('image',type=Path);p.add_argument('--base-url',default='http://127.0.0.1:18080')
    p.add_argument('--model',default='rapidocr-gpu');p.add_argument('--username',help='interactive session login instead of API key')
    p.add_argument('--password-file',type=Path,help='optional local credential file; never commit this file')
    p.add_argument('--output',type=Path)
    a=p.parse_args();base=a.base_url.rstrip('/')
    data=a.image.read_bytes()
    if len(data)>10*1024*1024:p.error('image must be <=10MB')
    mime=mimetypes.guess_type(a.image.name)[0]
    if mime not in ('image/jpeg','image/png'):p.error('use JPG/PNG')
    opener=urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
    headers={'Content-Type':'application/json'}
    if a.username:
        password=a.password_file.read_text().strip() if a.password_file else getpass.getpass('GPUStack password: ')
        opener.open(urllib.request.Request(base+'/auth/login',data=urllib.parse.urlencode({'username':a.username,'password':password}).encode()),timeout=30).close()
    else:
        key=os.getenv('GPUSTACK_API_KEY') or getpass.getpass('GPUStack API key: ')
        headers['Authorization']='Bearer '+key
    body={'model':a.model,'stream':False,'messages':[{'role':'user','content':[{'type':'image_url','image_url':{'url':f'data:{mime};base64,'+base64.b64encode(data).decode()}}]}]}
    start=time.perf_counter()
    with opener.open(urllib.request.Request(base+'/v1/chat/completions',headers=headers,data=json.dumps(body).encode()),timeout=120) as r: response=json.load(r)
    result=json.loads(response['choices'][0]['message']['content'])
    result['client_elapsed_ms']=round((time.perf_counter()-start)*1000,2)
    print(result['text'])
    print('\n'+json.dumps({k:v for k,v in result.items() if k not in ('text','lines')},ensure_ascii=False))
    if a.output:
        a.output.parent.mkdir(parents=True,exist_ok=True);a.output.write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')

if __name__=='__main__':
    try:main()
    except urllib.error.HTTPError as exc:
        print(f'HTTP {exc.code}: {exc.read().decode()}',file=__import__('sys').stderr);raise SystemExit(1)
