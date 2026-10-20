"""Read-only post-cleanup verification on controller 001."""
import base64,json,subprocess,sys,time,urllib.request
from pathlib import Path
ROOT=Path('/root/wtaobj/maas-framework')
sys.path.insert(0,str(ROOT/'scripts/maas/p0'))
from common import admin


def main():
    result={'checked_at':time.time(),'models':[]}
    target=json.loads(Path('/root/wtaobj/maas-runtime/target.json').read_text())
    image=Path('/root/wtaobj/service-validation/fixtures/upright.png').read_bytes()
    data=json.dumps({'image_base64':base64.b64encode(image).decode()}).encode()
    for ident in (1,target['model']):
        model=admin('/v2/models/'+str(ident))
        rows=admin('/v2/models/'+str(ident)+'/instances')['items']
        assert model['replicas']==model['ready_replicas']==2
        assert {x['worker_name'] for x in rows}=={'XZ-31-001','XZ-31-002'}
        assert len(rows)==2 and all(x['state']=='running' for x in rows)
        probes=[]
        for row in rows:
            url='http://'+row['worker_ip']+':'+str(row['port'])+'/ocr'
            with urllib.request.urlopen(urllib.request.Request(url,data=data,headers={'Content-Type':'application/json'}),timeout=30) as response:
                value=json.load(response)
                assert response.status==200 and len(value['lines'])==46
                probes.append({'worker':row['worker_name'],'instance':row['id'],'status':response.status,'lines':len(value['lines'])})
        result['models'].append({'name':model['name'],'replicas':2,'probes':probes})
    result['workers']=[{k:x.get(k) for k in ('id','name','state','unreachable')} for x in admin('/v2/workers')['items']]
    assert len(result['workers'])==2 and all(x['state']=='ready' and not x['unreachable'] for x in result['workers'])
    result['collector']=json.loads(Path('/root/wtaobj/maas-runtime/health.json').read_text())
    assert result['collector']['ok'] and time.time()-result['collector']['updated_at']<30
    result['collector_service']=subprocess.check_output(['systemctl','is-active','maas-collector.service'],text=True).strip()
    assert result['collector_service']=='active'
    result['temporary_model_remaining']=any(x['name']=='maas-ocr-002-offline' for x in admin('/v2/models')['items'])
    assert not result['temporary_model_remaining']
    names=subprocess.check_output(['docker','ps','-a','--format','{{.Names}}'],text=True).splitlines()
    result['temporary_api_executor_remaining']=[x for x in names if x in ('wta-maas-offline-api','wta-maas-offline-executor')]
    assert not result['temporary_api_executor_remaining']
    result['ok']=True
    print(json.dumps(result))


if __name__=='__main__':main()
