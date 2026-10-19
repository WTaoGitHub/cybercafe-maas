"""OCR validation engines with explicit rotation and guarded v4 direction checks."""
import os,time
from pathlib import Path
import cv2,numpy as np
from rapidocr_onnxruntime import RapidOCR
from rapidocr_onnxruntime.utils import reduce_max_side

VARIANTS=('v4_legacy','v4_upright','v4_guarded','v6_small')
def rotated_size(width,height,cw):return (height,width) if cw%180 else (width,height)
def rotate_points(points,width,height,cw):
    p=np.asarray(points,dtype=float);x=p[...,0];y=p[...,1]
    if cw==0:return p.copy()
    if cw==90:return np.stack([height-y,x],axis=-1)
    if cw==180:return np.stack([width-x,height-y],axis=-1)
    if cw==270:return np.stack([y,width-x],axis=-1)
    raise ValueError('rotation_cw must be 0,90,180,270')
def rotate_image(image,cw):
    if cw not in (0,90,180,270):raise ValueError('rotation_cw must be 0,90,180,270')
    return np.rot90(image,k=-(cw//90)).copy() if cw else image

class GuardClassifier:
    def __init__(self,original):self.original=original;self.angles=[]
    def __call__(self,crops):
        _,self.angles,elapsed=self.original(crops)
        return crops,self.angles,elapsed
    def __getattr__(self,key):return getattr(self.original,key)
class GuardRecognizer:
    def __init__(self,original,classifier):self.original=original;self.classifier=classifier;self.stats={}
    def __call__(self,crops,return_word_box=False):
        if return_word_box:raise ValueError('guarded mode does not support word boxes')
        initial,elapsed=self.original(crops,False)
        candidates=[i for i,r in enumerate(initial) if r[1]<.5 or (i<len(self.classifier.angles) and self.classifier.angles[i][0]=='180' and self.classifier.angles[i][1]>.9)]
        chosen=0;output=list(initial)
        if candidates:
            alternatives,extra=self.original([cv2.rotate(crops[i],cv2.ROTATE_180) for i in candidates],False);elapsed+=extra
            for i,alt in zip(candidates,alternatives):
                if alt[1]>initial[i][1]+.02:output[i]=alt;chosen+=1
        self.stats={'lines_tested_180':len(candidates),'lines_chosen_180':chosen,'recognized_lines':len(initial)}
        return output,elapsed
    def __getattr__(self,key):return getattr(self.original,key)

class Engine:
    def __init__(self,load_v6=True):
        self.v4=RapidOCR(det_use_cuda=True,cls_use_cuda=True,rec_use_cuda=True,intra_op_num_threads=2,inter_op_num_threads=2)
        self.original_cls=self.v4.text_cls;self.original_rec=self.v4.text_rec
        self.guarded_cls=GuardClassifier(self.original_cls);self.guarded_rec=GuardRecognizer(self.original_rec,self.guarded_cls)
        self.providers={}
        for name in ('text_det','text_cls','text_rec'):
            obj=getattr(self.v4,name);wrapper=obj.infer if hasattr(obj,'infer') else obj.session;pr=wrapper.session.get_providers();assert 'CUDAExecutionProvider' in pr
            self.providers['v4_'+name]=pr
        self.v6=None
        if load_v6:self.load_v6()
    def load_v6(self):
        if self.v6 is not None:return
        import onnxruntime as ort
        created=[];original=ort.InferenceSession
        class Tracked(original):
            def __init__(self,*a,**kw):super().__init__(*a,**kw);created.append(self)
        ort.InferenceSession=Tracked
        try:
            from paddleocr import PaddleOCR
            root=Path(os.getenv('MODEL_ROOT','/app/models'))
            self.v6=PaddleOCR(text_detection_model_name='PP-OCRv6_small_det',text_detection_model_dir=str(root/'PP-OCRv6_small_det_onnx'),text_recognition_model_name='PP-OCRv6_small_rec',text_recognition_model_dir=str(root/'PP-OCRv6_small_rec_onnx'),engine='onnxruntime',device='gpu:0',cpu_threads=2,use_doc_orientation_classify=False,use_doc_unwarping=False,use_textline_orientation=False,text_det_limit_side_len=736,text_det_limit_type='min',text_det_thresh=.3,text_det_box_thresh=.5,text_det_unclip_ratio=1.6,text_rec_score_thresh=.5,text_recognition_batch_size=6)
        finally:ort.InferenceSession=original
        assert len(created)==2,'Expected detection and recognition ONNX sessions'
        for i,s in enumerate(created):
            pr=s.get_providers();assert 'CUDAExecutionProvider' in pr;self.providers['v6_'+str(i)]=pr
    def run(self,array,variant='v4_guarded',rotation_cw=0):
        if variant not in VARIANTS:raise ValueError('unknown OCR variant')
        height,width=array.shape[:2];start=time.perf_counter();image=rotate_image(array,rotation_cw);stats={}
        if variant.startswith('v4_'):
            self.v4.use_cls=variant!='v4_upright'
            self.v4.text_cls=self.guarded_cls if variant=='v4_guarded' else self.original_cls
            self.v4.text_rec=self.guarded_rec if variant=='v4_guarded' else self.original_rec
            self.guarded_cls.angles=[];self.guarded_rec.stats={}
            values,_=self.v4(image)
            lines=[{'box':box,'text':text,'score':float(score)} for box,text,score in values or []]
            if variant=='v4_guarded':stats=dict(self.guarded_rec.stats)
        else:
            self.load_v6();rh=rw=1.
            if max(image.shape[:2])>2000:image,rh,rw=reduce_max_side(image,2000)
            result=list(self.v6.predict(image))[0]
            assert len(result['rec_polys'])==len(result['rec_texts'])==len(result['rec_scores'])
            lines=[{'box':(np.asarray(box)*[rw,rh]).tolist(),'text':text,'score':float(score)} for box,text,score in zip(result['rec_polys'],result['rec_texts'],result['rec_scores'])]
        ow,oh=rotated_size(width,height,rotation_cw)
        for line in lines:line['box']=rotate_points(line['box'],ow,oh,(-rotation_cw)%360).tolist()
        return {'text':'\n'.join(x['text'] for x in lines),'lines':lines,'variant':variant,'rotation_cw':rotation_cw,'processing_ms':round((time.perf_counter()-start)*1000,2),'direction_stats':stats}
