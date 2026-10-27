"""Consumer-compatible OCR facade, sharing SyncIngress admission and metering."""
import asyncio,base64,binascii,io,json,math,warnings
from pydantic import BaseModel,ConfigDict,Field,model_validator
from PIL import Image,UnidentifiedImageError
from .errors import ServiceError
from .image_fetch import PublicImageFetcher,image_url

MAX_PIXELS=20_000_000

class ConsumerOCRRequest(BaseModel):
    model_config=ConfigDict(extra='forbid',strict=True)
    model:str=Field(min_length=1,max_length=128)
    image_url:str|None=Field(default=None,min_length=1,max_length=4096)
    image_base64:str|None=Field(default=None,min_length=1)
    language:str|None=None
    return_boxes:bool=True
    request_id:str|None=Field(default=None,min_length=1,max_length=200)

    @model_validator(mode='after')
    def validate_options(self):
        if (self.image_url is None)==(self.image_base64 is None):raise ValueError('Choose exactly one image source')
        if self.language not in (None,'auto','zh','en'):raise ValueError('Supported language hints: auto, zh, en')
        if self.request_id and any(ord(c)<32 or ord(c)==127 for c in self.request_id):raise ValueError('Invalid request id')
        return self


def decode_base64(value,limit):
    media=None
    if value.startswith('data:'):
        try:prefix,value=value.split(',',1)
        except ValueError:raise ServiceError('IMAGE_BASE64','Invalid image data URL',422) from None
        if prefix not in ('data:image/png;base64','data:image/jpeg;base64'):
            raise ServiceError('IMAGE_FORMAT','Only PNG and JPEG data URLs are supported',415)
        media=prefix[5:-7]
    if len(value)>4*((limit+2)//3):raise ServiceError('INPUT_SIZE','Image exceeds decoded byte limit',413)
    try:raw=base64.b64decode(value,validate=True)
    except (ValueError,binascii.Error):raise ServiceError('IMAGE_BASE64','Invalid base64 image',422) from None
    if len(raw)>limit:raise ServiceError('INPUT_SIZE','Image exceeds decoded byte limit',413)
    return raw,media


def validate_image(raw,declared=None):
    try:
        with warnings.catch_warnings():
            warnings.simplefilter('error',Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(raw),formats=['PNG','JPEG']) as image:
                if image.width*image.height>MAX_PIXELS:
                    raise ServiceError('IMAGE_PIXELS','Image exceeds 20 million pixels',413)
                if getattr(image,'is_animated',False):raise ServiceError('IMAGE_FORMAT','Animated images are unsupported',415)
                media={'PNG':'image/png','JPEG':'image/jpeg'}[image.format]
                if declared and declared!=media:raise ServiceError('IMAGE_FORMAT','Data URL media type does not match the image',415)
                image.verify()
            with Image.open(io.BytesIO(raw),formats=['PNG','JPEG']) as image:image.load()
        return media
    except ServiceError:raise
    except (Image.DecompressionBombError,Image.DecompressionBombWarning):
        raise ServiceError('IMAGE_PIXELS','Image dimensions exceed the supported limit',413) from None
    except (UnidentifiedImageError,OSError,ValueError,SyntaxError,KeyError):
        raise ServiceError('IMAGE_INVALID','Input must be a valid PNG or JPEG',422) from None


def consumer_output(raw,return_boxes=True):
    try:
        envelope=json.loads(raw);value=json.loads(envelope['choices'][0]['message']['content'])
        lines=value['lines']
        if not isinstance(lines,list):raise ValueError()
        output=[]
        for line in lines:
            if not isinstance(line,dict) or not isinstance(line.get('text'),str):raise ValueError()
            item={'text':line['text']}
            if 'score' in line:
                score=line['score']
                if isinstance(score,bool) or not isinstance(score,(int,float)) or not math.isfinite(score) or not 0<=score<=1:raise ValueError()
                item['confidence']=score
            if return_boxes and 'box' in line:
                polygon=line['box']
                if not isinstance(polygon,list) or len(polygon)!=4:raise ValueError()
                for point in polygon:
                    if not isinstance(point,list) or len(point)!=2 or any(isinstance(x,bool) or not isinstance(x,(int,float)) or not math.isfinite(x) for x in point):raise ValueError()
                item['polygon']=polygon
            output.append(item)
        return json.dumps({'lines':output},ensure_ascii=False,allow_nan=False).encode()
    except (ValueError,KeyError,IndexError,TypeError,OverflowError):
        raise ServiceError('OUTPUT_INVALID','OCR output does not match the consumer contract',502) from None


class ConsumerOCR:
    def __init__(self,ingress,fetcher=None):self.ingress=ingress;self.fetcher=fetcher or PublicImageFetcher()

    async def infer(self,who,request):
        limit=self.ingress.service.settings.max_input_bytes
        # Fast syntax checks do not download anything or admit a GPU request.
        if request.image_url:image_url(request.image_url)
        async def prepare():
            if request.image_base64 is not None:
                raw,declared=decode_base64(request.image_base64,limit)
            else:raw,declared=await self.fetcher.fetch(request.image_url,limit),None
            media=await asyncio.to_thread(validate_image,raw,declared)
            return {'model':request.model,'stream':False,'messages':[{'role':'user','content':[
                {'type':'image_url','image_url':{'url':'data:'+media+';base64,'+base64.b64encode(raw).decode()}}]}]}
        return await self.ingress.infer(who,{'model':request.model,'stream':False},
            prepare_input=prepare,map_output=lambda raw:consumer_output(raw,request.return_boxes))
