import copy,time
import pytest
from maas_platform.errors import ServiceError
from maas_platform.versioning import register_pin,verify_pin


def fixture():
 r={'id':1,'name':'ocr-r03','access_policy':'authed'}
 t=[{'model_id':2,'provider_id':None,'weight':100}]
 m={'id':2,'name':'ocr-r03','backend':'OCR-custom','backend_version':'0.3','env':{'MODE':'guarded'}}
 b={'backend_name':'OCR-custom','version_configs':{'0.3':{'image_name':'local/ocr:fixed'}}}
 p=register_pin('ocr-r03',r,t,m,b,'sha256:'+'a'*64,['001','002'])
 inv={name:{'image_name':p.image_name,'image_id':p.image_id,'observed_at':100} for name in ['001','002']}
 return p,r,t,m,b,inv


def check(values):
 p,r,t,m,b,inv=values
 return verify_pin(p,{'version':'v2.2.3'},r,t,m,b,inv,now=110)


def test_pin_matches_and_ignores_transient_replica_status():
 vals=fixture();vals[3]['ready_replicas']=0;vals[3]['replicas']=4
 assert check(vals)['verified']


@pytest.mark.parametrize('field',['env','backend_version','backend_parameters','local_path'])
def test_changed_model_spec_rejected(field):
 vals=fixture();vals[3][field]='changed'
 with pytest.raises((ServiceError,KeyError)):check(vals)


def test_retarget_or_new_fallback_rejected():
 vals=fixture();vals[2].append({'model_id':3,'weight':100})
 with pytest.raises(ServiceError):check(vals)
 vals=fixture();vals[2][0]['fallback_status_codes']=['5xx']
 with pytest.raises(ServiceError):check(vals)


def test_image_tag_reuse_and_new_worker_rejected():
 vals=fixture();vals[5]['002']['image_id']='sha256:'+'b'*64
 with pytest.raises(ServiceError) as e:check(vals)
 assert e.value.code=='MODEL_IMAGE_CHANGED'
 vals=fixture();vals[5]['003']=vals[5]['001']
 with pytest.raises(ServiceError):check(vals)


def test_stale_inventory_rejected():
 vals=fixture();vals[5]['001']['observed_at']=0
 with pytest.raises(ServiceError) as e:check(vals)
 assert e.value.code=='IMAGE_INVENTORY_STALE'


def test_public_route_and_mutable_external_weights_rejected():
 vals=fixture();vals[1]['access_policy']='public'
 with pytest.raises(ServiceError):check(vals)
 p,r,t,m,b,inv=fixture()
 with pytest.raises(ServiceError):register_pin('x',r,t,m,b,p.image_id,['001'],weights_policy='mutable-path')


def test_other_gpustack_version_rejected():
 p,r,t,m,b,inv=fixture()
 with pytest.raises(ServiceError) as e:verify_pin(p,{'version':'v2.3.0'},r,t,m,b,inv,now=110)
 assert e.value.code=='GPUSTACK_VERSION_CHANGED'
