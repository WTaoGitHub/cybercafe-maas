"""One-shot layer probes for existing model18. Never repeats an uncertain request.
No replica changes, resume, restart, or dispatch of queued MaaS tasks.
"""
import base64,hashlib,json,os,sys,time,urllib.request,urllib.error,uuid
from pathlib import Path
ROOT=Path('/root/wtaobj/maas-framework');LIVE=Path('/root/wtaobj/maas-live')
sys.path[:0]=[str(ROOT),'/root/wtaobj']


def main():
    from gpustack_admin import api
    folder=LIVE/'e3/debug-20261029';folder.mkdir(mode=0o700,exist_ok=False)
    rows=api('/v2/models/18/instances')['items'];model=api('/v2/models/18')
    assert len(rows)==model['replicas']==4 and all(r['state']=='running' for r in rows)
    key=(LIVE/'secrets/pilot-key').read_text().strip();image=(LIVE/'probes/upright.png').read_bytes()
    payload={'model':model['name'],'stream':False,'messages':[{'role':'user','content':[{'type':'image_url','image_url':{'url':'data:image/png;base64,'+base64.b64encode(image).decode()}}]}]}
    opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
    report={'started_at':time.time(),'records':[],'image_sha256':hashlib.sha256(image).hexdigest(),'models_not_changed':True,'admission_not_resumed':True}
    def save():
        p=folder/'layers.json';tmp=p.with_suffix('.new');fd=os.open(tmp,os.O_CREAT|os.O_WRONLY|os.O_TRUNC,0o600)
        with os.fdopen(fd,'w') as f:json.dump(report,f,indent=2);f.flush();os.fsync(f.fileno())
        os.replace(tmp,p)
    def request(layer,url,instance=None,proxy=False,infer=True):
        ident=str(uuid.uuid4());r={'layer':layer,'url':url,'instance_id':instance,'request_id':ident,'intent_at':time.time()};report['records'].append(r);save()
        headers={'X-Request-ID':ident,'Content-Type':'application/json'}
        if proxy or layer=='gateway':headers.update(Authorization='Bearer '+key,**{'X-Higress-Llm-Model':model['name']})
        if proxy:headers['X-GPUStack-Model-Instance']='model-18-'+str(instance)+'.static'
        start=time.monotonic()
        try:
            with opener.open(urllib.request.Request(url,data=json.dumps(payload).encode() if infer else None,headers=headers),timeout=30) as response:
                value=json.load(response);r.update(status=response.status,seconds=time.monotonic()-start)
            assert r['status']==200
            if infer:
                content=json.loads(value['choices'][0]['message']['content']);assert len(content['lines'])==46
                r.update(lines=46,node=content.get('node'))
            else:r['health_ok']=value.get('ok') is True;assert r['health_ok']
            r['state']='PASS';save();print(json.dumps(r),flush=True)
        except Exception as exc:
            r.update(state='FAILED_STOP',error_class=type(exc).__name__,http_status=getattr(exc,'code',None));report['state']='FAILED_STOP';save();raise
    save()
    for row in sorted(rows,key=lambda r:r['id']):
        request('direct-engine','http://'+row['worker_ip']+':'+str(row['port'])+'/v1/chat/completions',row['id'])
        request('worker-proxy-health','http://'+row['worker_ip']+':10150/health',row['id'],True,False)
        request('worker-proxy-inference','http://'+row['worker_ip']+':10150/v1/chat/completions',row['id'],True)
    for _ in range(4):request('gateway','http://127.0.0.1:18080/v1/chat/completions')
    report.update(state='COMPLETE',finished_at=time.time());save()
    print(json.dumps({'state':'COMPLETE','probe_count':len(report['records']),'no_old_request_replayed':True}))


if __name__=='__main__':main()
