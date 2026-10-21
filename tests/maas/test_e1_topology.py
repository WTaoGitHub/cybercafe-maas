import copy
import pytest
from maas_platform.e1.topology import collect_subset, verify_replica
from maas_platform.versioning import register_pin


def fixture(worker='002', logical_model='ocr', now=100):
    route={'id':11,'name':'ocr-'+worker,'access_policy':'authed'}
    targets=[{'model_id':12,'weight':100}]
    model={'id':12,'backend':'OCR','backend_version':'r2','replicas':1,
           'worker_selector':{'maas-node':worker}}
    backend={'backend_name':'OCR','version_configs':{'r2':{'image_name':'local/ocr:r2'}}}
    image='sha256:'+'a'*64
    pin=register_pin('r2',route,targets,model,backend,image,[worker])
    control={'route':route,'targets':targets,'model':model,'backend':backend,'version':{'version':'v2.2.3'},
      'workers':[{'name':worker,'id':2,'state':'ready','labels':{'maas-node':worker}}],
      'instances':[{'id':22,'worker_id':2,'worker_name':worker,'state':'running'}]}
    inv={worker:{'worker':worker,'image_name':pin.image_name,'image_id':image,
                 'running_image_ids':[image],'paused':False,'observed_at':now}}
    gateway={'observed_at':now,'route_id':11,'instance_ids':[22],'max_attempts':1,
             'policies':{'higress.io/proxy-next-upstream':'off'}}
    spec={'logical_model':logical_model,'revision':'r2','pin':pin,'slots':1}
    return spec,control,inv,gateway


def test_verified_single_worker_alias():
    s,c,i,g=fixture()
    r=verify_replica(s['logical_model'],'r2',s['pin'],c,i,g,now=100)
    assert r.worker=='002' and r.instance_id==22 and r.slots==1


@pytest.mark.parametrize('fault',['version','worker','maintenance','selector-collision','instance',
 'multiple-instances','image','revision','fallback','retry','stale-gateway','stale-image','distributed'])
def test_fail_closed(fault):
    s,c,i,g=fixture()
    if fault=='version':c['version']['version']='v2.3.0'
    if fault=='worker':c['workers'][0]['state']='not_ready'
    if fault=='maintenance':c['workers'][0]['maintenance']={'enabled':True}
    if fault=='selector-collision':c['workers'].append(copy.deepcopy(c['workers'][0]))
    if fault=='instance':c['instances'][0]['worker_name']='001'
    if fault=='multiple-instances':c['instances'].append(copy.deepcopy(c['instances'][0]))
    if fault=='image':i['002']['running_image_ids']=['sha256:'+'b'*64]
    if fault=='revision':s['revision']='other'
    if fault=='fallback':c['targets'][0]['fallback_status_codes']=[503]
    if fault=='retry':g['max_attempts']=3
    if fault=='stale-gateway':g['observed_at']=60
    if fault=='stale-image':i['002']['observed_at']=60
    if fault=='distributed':c['model']['distributed_inference_across_workers']=True
    with pytest.raises(Exception):verify_replica(s['logical_model'],s['revision'],s['pin'],c,i,g,now=100)


def test_one_alias_failure_does_not_revoke_another_model():
    a=fixture('001','model1');b=fixture('002','model2');b[1]['workers'][0]['state']='not_ready'
    values={s['pin'].route_name:(c,i,g) for s,c,i,g in (a,b)}
    replicas,errors=collect_subset([a[0],b[0]],lambda s:values[s['pin'].route_name],now=100)
    assert [r.logical_model for r in replicas]==['model1']
    assert errors==[{'logical_model':'model2','route':'ocr-002','code':'E1_WORKER_UNAVAILABLE'}]
