import json,time
from pathlib import Path
import onnxruntime as ort
sessions=[];original=ort.InferenceSession
class Tracked(original):
    def __init__(self,*a,**kw):
        super().__init__(*a,**kw);sessions.append(self)
ort.InferenceSession=Tracked
from paddleocr import PaddleOCR
start=time.perf_counter()
ocr=PaddleOCR(text_detection_model_name='PP-OCRv6_small_det',text_detection_model_dir='/experiment/models/PP-OCRv6_small_det_onnx',text_recognition_model_name='PP-OCRv6_small_rec',text_recognition_model_dir='/experiment/models/PP-OCRv6_small_rec_onnx',engine='onnxruntime',device='gpu:0',use_doc_orientation_classify=False,use_doc_unwarping=False,use_textline_orientation=False,text_det_limit_side_len=736,text_det_limit_type='min',text_det_box_thresh=.5,text_det_thresh=.3,text_det_unclip_ratio=1.6,text_rec_score_thresh=.5,text_recognition_batch_size=6,cpu_threads=2)
print('initialized_seconds',time.perf_counter()-start,flush=True)
result=list(ocr.predict('/experiment/dataset/images/rctw17_image_1941.jpg'))[0]
print('keys',list(result.keys()));print('text',result.get('rec_texts'));print('sessions',[(s.get_providers(),s.get_inputs()[0].shape,[o.shape for o in s.get_outputs()]) for s in sessions])
Path('/experiment/probe-result.json').write_text(result.json if isinstance(result.json,str) else json.dumps(result.json,ensure_ascii=False,default=str))
