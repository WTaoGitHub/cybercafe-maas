"""Offline isolated GPU experiment; never changes the serving deployment."""
import argparse,base64,gc,hashlib,json,time,unicodedata,subprocess
from pathlib import Path
import cv2,numpy as np
from PIL import Image
from rapidocr_onnxruntime import RapidOCR
VARIANTS={
 'A_default':{},
 'B_resolution1536':{'det_limit_side_len':1536},
 'C_tiles1024':{'tile_size':1024,'tile_overlap':192},
 'D_score030':{'text_score':.3},
 'E_rotate90':{'rotate90':True},
}
def starts(length,size,overlap):
    if length<=size:return [0]
    return list(dict.fromkeys(list(range(0,length-size,size-overlap))+[length-size]))
def canonical(text):return ''.join(c for c in unicodedata.normalize('NFKC',text).casefold() if not c.isspace())
def merge(candidates):
    # Prefer complete boxes over boxes cut by an interior crop edge, then confidence.
    keep=[]
    from difflib import SequenceMatcher
    for item in sorted(candidates,key=lambda a:(not a.get('edge_cut',False),a['score']),reverse=True):
        p=np.asarray(item['box'],np.float32);area=abs(cv2.contourArea(p));duplicate=False
        for other in keep:
            q=np.asarray(other['box'],np.float32);qa=abs(cv2.contourArea(q))
            inter,_=cv2.intersectConvexConvex(cv2.convexHull(p),cv2.convexHull(q))
            if inter/max(min(area,qa),1e-6)<.6:continue
            a,b=canonical(item['text']),canonical(other['text'])
            if a==b or (a and b and (a in b or b in a)) or SequenceMatcher(None,a,b).ratio()>=.6:
                duplicate=True;break
        if not duplicate:keep.append(item)
    return sorted(keep,key=lambda a:(min(p[1] for p in a['box']),min(p[0] for p in a['box'])))
def recognize(engine,array,config):
    h,w=array.shape[:2];candidates=[];calls=0
    def run(crop,x=0,y=0,rotation=False):
        nonlocal calls
        values,_=engine(crop);calls+=1
        for box,text,confidence in values or []:
            p=np.asarray(box,dtype=float)
            if rotation:
                # np.rot90: original x = W - 1 - rotated_y; original y = rotated_x.
                p=np.stack([w-1-p[:,1],p[:,0]],axis=1)
                edge=False
            else:
                ch,cw=crop.shape[:2]
                edge=(x>0 and p[:,0].min()<4) or (y>0 and p[:,1].min()<4) or (x+cw<w and p[:,0].max()>cw-5) or (y+ch<h and p[:,1].max()>ch-5)
                p+=np.array([x,y])
            candidates.append({'box':p.tolist(),'text':text,'score':float(confidence),'edge_cut':bool(edge)})
    if config.get('rotate90'):run(np.rot90(array).copy(),rotation=True)
    elif config.get('tile_size'):
        size=config['tile_size'];overlap=config['tile_overlap']
        for y in starts(h,size,overlap):
            for x in starts(w,size,overlap):run(array[y:y+size,x:x+size].copy(),x,y)
    else:run(array)
    raw_count=len(candidates);lines=merge(candidates) if config.get('tile_size') else candidates
    for line in lines:line.pop('edge_cut',None)
    return {'text':'\n'.join(x['text'] for x in lines),'lines':lines,'engine_calls':calls,'raw_line_count':raw_count,'deduplicated':raw_count-len(lines)}
def main():
    p=argparse.ArgumentParser();p.add_argument('--dataset',type=Path,required=True);p.add_argument('--output',type=Path,required=True);p.add_argument('--split',choices=['calibration','evaluation'],required=True);p.add_argument('--variants',nargs='+',choices=list(VARIANTS),required=True);a=p.parse_args()
    records={r['id']:r for r in map(json.loads,(a.dataset/'manifest.jsonl').read_text().splitlines())};ids=json.loads((a.dataset/'splits.json').read_text())[a.split]
    node=subprocess.check_output(['nvidia-smi','--query-gpu=uuid','--format=csv,noheader'],text=True).strip()
    for variant in a.variants:
        config=VARIANTS[variant];kwargs={k:v for k,v in config.items() if k in ['det_limit_side_len','text_score']}
        engine=RapidOCR(det_use_cuda=True,cls_use_cuda=True,rec_use_cuda=True,intra_op_num_threads=2,inter_op_num_threads=2,**kwargs)
        providers={}
        for name in ['text_det','text_cls','text_rec']:
            obj=getattr(engine,name);wrapper=obj.infer if hasattr(obj,'infer') else obj.session;providers[name]=wrapper.session.get_providers()
        assert all('CUDAExecutionProvider' in v for v in providers.values())
        dest=a.output/variant/a.split;dest.mkdir(parents=True,exist_ok=True)
        info={'variant':variant,'parameters':config,'node':node,'providers':providers,'image_count':len(ids),'gpu':subprocess.check_output(['nvidia-smi','--query-gpu=name,driver_version','--format=csv,noheader'],text=True).strip(),'started_at':time.strftime('%Y-%m-%dT%H:%M:%SZ',time.gmtime())}
        (dest/'run-info.json').write_text(json.dumps(info,indent=2)+'\n')
        # One unmeasured warm-up per variant; no service / HTTP / image decoding in inference_ms.
        first=records[ids[0]];arr=np.asarray(Image.open(a.dataset/first['image']).convert('RGB'))[:,:,::-1].copy();recognize(engine,arr,config)
        for n,ident in enumerate(ids,1):
            target=dest/(ident+'.json')
            if target.exists():continue
            row={'id':ident,'source':records[ident]['source'],'split':a.split,'variant':variant};t=time.perf_counter()
            try:
                raw=(a.dataset/records[ident]['image']).read_bytes();assert hashlib.sha256(raw).hexdigest()==records[ident]['sha256']
                import io
                array=np.asarray(Image.open(io.BytesIO(raw)).convert('RGB'))[:,:,::-1].copy();start=time.perf_counter()
                result=recognize(engine,array,config);result.update(elapsed_ms=round((time.perf_counter()-start)*1000,2),node=node)
                row.update(ok=True,result=result,total_ms=round((time.perf_counter()-t)*1000,2))
            except Exception as e:row.update(ok=False,error=type(e).__name__+': '+str(e))
            target.write_text(json.dumps(row,ensure_ascii=False,indent=2)+'\n')
            print(variant,a.split,n,len(ids),ident[:28],row['ok'],flush=True)
        del engine;gc.collect()
if __name__=='__main__':main()
