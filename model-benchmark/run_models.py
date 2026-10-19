"""Compare pinned official OCR ONNX models in an isolated GPU container."""
import argparse,gc,hashlib,importlib.metadata as metadata,json,os,subprocess,threading,time
from pathlib import Path
import numpy as np
from PIL import Image
import onnxruntime as ort
ROOT=Path('/experiment');DATA=ROOT/'dataset'
MODELS={'M1_v6_small':('PP-OCRv6_small_det','PP-OCRv6_small_rec'),'M2_v6_medium':('PP-OCRv6_medium_det','PP-OCRv6_medium_rec'),'M3_v5_server':('PP-OCRv5_server_det','PP-OCRv5_server_rec'),'A_v4_no_cls':None}
PARAMS={'max_side_len':2000,'text_det_limit_side_len':736,'text_det_limit_type':'min','text_det_thresh':.3,'text_det_box_thresh':.5,'text_det_unclip_ratio':1.6,'text_rec_score_thresh':.5,'text_recognition_batch_size':6,'use_textline_orientation':False,'use_doc_orientation_classify':False,'use_doc_unwarping':False}
SESSIONS=[];ORIGINAL=ort.InferenceSession
class TrackedSession(ORIGINAL):
    def __init__(self,*a,**kw):
        super().__init__(*a,**kw);SESSIONS.append(self)
ort.InferenceSession=TrackedSession

def gpu_sample():
    try:
        lines=subprocess.check_output(['nvidia-smi','--query-compute-apps=pid,used_gpu_memory','--format=csv,noheader,nounits'],text=True).splitlines()
        return {int(line.split(',')[0]):int(line.split(',')[1]) for line in lines if len(line.split(','))==2 and line.split(',')[1].strip().isdigit()}
    except Exception:return {}
class MemoryMonitor:
    def __init__(self):
        self.baseline=gpu_sample();self.peak=0;self.samples=[];self.stop=threading.Event();self.thread=threading.Thread(target=self.run,daemon=True)
    def run(self):
        while not self.stop.is_set():
            sample=gpu_sample();new={str(p):m for p,m in sample.items() if p not in self.baseline}
            self.peak=max(self.peak,sum(new.values()));self.samples.append({'unix_time':time.time(),'new_process_memory_mib':new});self.stop.wait(1)
    def finish(self):self.stop.set();self.thread.join();return {'sample_interval_s':1,'baseline_processes':self.baseline,'new_process_peak_mib':self.peak,'samples':self.samples,'note':'Sampled GPU process allocations, not instantaneous CUDA operator peak; excludes pre-existing serving PIDs.'}

def main():
    p=argparse.ArgumentParser();p.add_argument('--variant',choices=list(MODELS),required=True);p.add_argument('--splits',nargs='+',default=['calibration','evaluation']);p.add_argument('--output',type=Path,default=ROOT/'model-results');a=p.parse_args()
    records={r['id']:r for r in map(json.loads,(DATA/'manifest.jsonl').read_text().splitlines())};splits=json.loads((DATA/'splits.json').read_text());folder=a.output/a.variant;folder.mkdir(parents=True,exist_ok=True)
    monitor=MemoryMonitor();monitor.thread.start()
    versions={k:metadata.version(k) for k in ['paddleocr','paddlex','rapidocr-onnxruntime','onnxruntime-gpu','numpy','opencv-python']}
    node=subprocess.check_output(['nvidia-smi','--query-gpu=uuid','--format=csv,noheader'],text=True).strip()
    start=time.perf_counter();names=MODELS[a.variant]
    if names:
        from paddleocr import PaddleOCR
        engine=PaddleOCR(text_detection_model_name=names[0],text_detection_model_dir=str(ROOT/'models'/(names[0]+'_onnx')),text_recognition_model_name=names[1],text_recognition_model_dir=str(ROOT/'models'/(names[1]+'_onnx')),engine='onnxruntime',device='gpu:0',cpu_threads=2,**{k:v for k,v in PARAMS.items() if k!='max_side_len'})
    else:
        from rapidocr_onnxruntime import RapidOCR
        engine=RapidOCR(det_use_cuda=True,cls_use_cuda=True,rec_use_cuda=True,use_cls=False,intra_op_num_threads=2,inter_op_num_threads=2)
    init_seconds=time.perf_counter()-start
    from rapidocr_onnxruntime.utils import reduce_max_side
    def infer(array):
        if not names:
            values,_=engine(array);lines=[{'box':box,'text':text,'score':float(confidence)} for box,text,confidence in values or []];extra={}
        else:
            # Reuse the old serving pipeline's exact initial resize and inverse scaling.
            image=array;rh=rw=1.
            if max(array.shape[:2])>2000:image,rh,rw=reduce_max_side(array,2000)
            out=list(engine.predict(image))[0];lines=[]
            for box,text,confidence in zip(out['rec_polys'],out['rec_texts'],out['rec_scores']):
                mapped=np.asarray(box,dtype=float)*np.array([rw,rh]);lines.append({'box':mapped.tolist(),'text':text,'score':float(confidence)})
            extra={'effective_text_det_params':out['text_det_params'],'model_settings':out['model_settings'],'pre_resize_shape':list(image.shape),'detected_boxes':len(out['dt_polys'])}
        return {'text':'\n'.join(x['text'] for x in lines),'lines':lines,'node':node,'engine_calls':1,'deduplicated':0,**extra}
    first=records[splits[a.splits[0]][0]];arr=np.asarray(Image.open(DATA/first['image']).convert('RGB'))[:,:,::-1].copy();infer(arr)
    providers=[{'providers':s.get_providers(),'input_shape':s.get_inputs()[0].shape,'output_shapes':[o.shape for o in s.get_outputs()]} for s in SESSIONS]
    if names:assert len(providers)==2 and all('CUDAExecutionProvider' in s['providers'] for s in providers),providers
    else:
        providers=[]
        for name in ['text_det','text_rec']:
            obj=getattr(engine,name);wrapper=obj.infer if hasattr(obj,'infer') else obj.session;pr=wrapper.session.get_providers();assert 'CUDAExecutionProvider' in pr;providers.append({'component':name,'providers':pr})
    info={'variant':a.variant,'models':names,'parameters':PARAMS,'providers':providers,'versions':versions,'node':node,'initialization_seconds':init_seconds,'started_at':time.strftime('%Y-%m-%dT%H:%M:%SZ',time.gmtime()),'prewarm_images':1,'concurrency':1,'retries':0}
    try:
        for split in a.splits:
            dest=folder/split;dest.mkdir(parents=True,exist_ok=True);(dest/'run-info.json').write_text(json.dumps(info,indent=2)+'\n')
            for n,ident in enumerate(splits[split],1):
                path=dest/(ident+'.json')
                if path.exists():continue
                rec=records[ident];row={'id':ident,'source':rec['source'],'split':split,'variant':a.variant,'image_sha256':rec['sha256']}
                try:
                    raw=(DATA/rec['image']).read_bytes();assert hashlib.sha256(raw).hexdigest()==rec['sha256']
                    import io
                    array=np.asarray(Image.open(io.BytesIO(raw)).convert('RGB'))[:,:,::-1].copy();start=time.perf_counter();result=infer(array);result['elapsed_ms']=round((time.perf_counter()-start)*1000,2);row.update(ok=True,result=result)
                except Exception as e:row.update(ok=False,error=type(e).__name__+': '+str(e))
                path.write_text(json.dumps(row,ensure_ascii=False,indent=2,default=str)+'\n');print(a.variant,split,n,len(splits[split]),ident[:28],row['ok'],flush=True)
    finally:(folder/'memory.json').write_text(json.dumps(monitor.finish(),indent=2)+'\n')
if __name__=='__main__':main()
