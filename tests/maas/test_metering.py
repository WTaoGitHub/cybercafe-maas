import pytest
from maas_platform.metering import tokens_from_response,receipt,single_image_ocr


@pytest.mark.parametrize('usage',[None,{}, {'prompt_tokens':1}, {'prompt_tokens':True,'completion_tokens':1}, {'prompt_tokens':-1,'completion_tokens':1}, {'prompt_tokens':1.2,'completion_tokens':2}, {'prompt_tokens':1,'completion_tokens':2,'total_tokens':4}])
def test_no_estimated_tokens(usage):
    tokens=tokens_from_response({'usage':usage})
    assert tokens['status']=='unavailable' and tokens['input_tokens'] is tokens['output_tokens'] is None


@pytest.mark.parametrize('a,b',[(0,0),(10,20)])
def test_actual_usage_and_stable_id(a,b):
    tokens=tokens_from_response({'usage':{'prompt_tokens':a,'completion_tokens':b,'total_tokens':a+b}})
    assert tokens['status']=='available' and tokens['input_tokens']==a and tokens['output_tokens']==b
    r=receipt('task','task_x','ocr.default','r1',tokens)
    assert r['id']=='usage_task_task_x' and r['successful_images']==1
    assert r['id']==receipt('task','task_x','ocr.default','r1')['id']


def test_unverified_chat_is_not_a_successful_image():
    assert not single_image_ocr({'messages':[]},{'choices':[]})
