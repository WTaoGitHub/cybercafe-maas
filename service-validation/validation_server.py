"""Isolated OpenAI-compatible OCR endpoint for direction and service validation."""
import argparse,base64,io,json,os,re,subprocess,threading,time,uuid
import numpy as np
from PIL import Image
from fastapi import FastAPI,HTTPException
from fastapi.responses import StreamingResponse
import uvicorn
from engine import Engine,VARIANTS
from ocr_protocol import completion_events
parser=argparse.ArgumentParser();parser.add_argument('--port',type=int,default=18830);parser.add_argument('--host',default='0.0.0.0');parser.add_argument('--model',default='wta-ocr-validation');args=parser.parse_args()
engine=Engine();lock=threading.Lock();app=FastAPI()
node=subprocess.check_output(['nvidia-smi','--query-gpu=uuid','--format=csv,noheader'],text=True).strip()
@app.get('/health')
def health():return {'ok':True,'node':node,'variants':VARIANTS,'providers':engine.providers,'unix_time':time.time()}
@app.get('/v1/models')
def models():return {'object':'list','data':[{'id':args.model,'object':'model','owned_by':'cybercafe'}]}
def recognize(encoded,variant,rotation):
    try:
        if variant not in VARIANTS or type(rotation) is not int or rotation not in (0,90,180,270):raise ValueError('invalid variant or rotation_cw')
        raw=base64.b64decode(encoded.split(',',1)[-1],validate=True)
        if len(raw)>10*1024*1024:raise ValueError('image exceeds 10MB')
        image=Image.open(io.BytesIO(raw))
        if image.width*image.height>20_000_000:raise ValueError('image exceeds 20 megapixels')
        array=np.asarray(image.convert('RGB'))[:,:,::-1].copy()
    except Exception as exc:raise HTTPException(400,str(exc))
    entered=time.perf_counter()
    with lock:
        wait=(time.perf_counter()-entered)*1000
        result=engine.run(array,variant,rotation)
    result.update(node=node,queue_ms=round(wait,2),elapsed_ms=round(wait+result['processing_ms'],2));return result
@app.post('/ocr')
def ocr(body:dict):return recognize(body.get('image_base64',''),body.get('ocr_variant','v4_guarded'),body.get('rotation_cw',0))
@app.post('/v1/chat/completions')
def chat(body:dict):
    encoded=None;variant=body.get('ocr_variant','v4_guarded');rotation=body.get('rotation_cw',0)
    for message in body.get('messages',[]):
        content=message.get('content')
        if not isinstance(content,list):continue
        for part in content:
            if part.get('type')=='image_url':encoded=part.get('image_url',{}).get('url','')
            if part.get('type')=='text':
                text=part.get('text','');match=re.search(r'ocr_variant=(v4_legacy|v4_upright|v4_guarded|v6_small)',text)
                if match:variant=match.group(1)
                match=re.search(r'rotation_cw=(0|90|180|270)(?:\D|$)',text)
                if match:rotation=int(match.group(1))
    if not encoded or not encoded.startswith('data:image/'):raise HTTPException(400,'supply a base64 image data URL')
    result=recognize(encoded,variant,rotation);cid='ocr-'+uuid.uuid4().hex;created=int(time.time())
    if body.get('stream'):return StreamingResponse(completion_events(result,cid,created,args.model),media_type='text/event-stream',headers={'Cache-Control':'no-cache','X-Accel-Buffering':'no'})
    return {'id':cid,'object':'chat.completion','created':created,'model':args.model,'choices':[{'index':0,'message':{'role':'assistant','content':json.dumps(result,ensure_ascii=False)},'finish_reason':'stop'}]}
uvicorn.run(app,host=args.host,port=args.port)
