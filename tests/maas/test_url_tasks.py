import asyncio
import pytest
from pydantic import ValidationError
from maas_platform.ocr_connector import OCRAdapter
from maas_platform.errors import ServiceError
from maas_platform.task_image_url import fetch_image,lock_key


@pytest.mark.parametrize('image',[{'url':'https://example.org/a','data_url':'x'}, {'url':'https://example.org/a','artifact_id':'x'}, {'url':'x','extra':1}, {'url':''}, {'url':'x'*4097}])
def test_three_sources_mutually_exclusive(image):
    with pytest.raises(ValidationError):OCRAdapter().validate({'image':image},{})


def test_url_schema_and_lock_key_namespace():
    assert OCRAdapter().validate({'image':{'url':'https://example.org/a'}},{})[2] is None
    assert lock_key('idem','ab','c')!=lock_key('idem','a','bc')
    assert lock_key('idem','a','b')!=lock_key('project','a','b')
    assert -(2**63)<=lock_key('global','0')<2**63


def test_absolute_download_deadline(monkeypatch):
    monkeypatch.setattr('maas_platform.task_image_url.DOWNLOAD_SECONDS',.01)
    async def slow(*args):await asyncio.sleep(1)
    monkeypatch.setattr('maas_platform.task_image_url.PublicImageFetcher.fetch',slow)
    with pytest.raises(ServiceError) as exc:asyncio.run(fetch_image('https://example.org/a',100))
    assert (exc.value.code,exc.value.status)==('IMAGE_FETCH_TIMEOUT',408)
