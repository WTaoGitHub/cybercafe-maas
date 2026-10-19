"""Inspect one calibration page to explain the v4 classification regression."""
import copy,json
from pathlib import Path
import numpy as np
from PIL import Image
from rapidocr_onnxruntime import RapidOCR
root=Path('/experiment');ident='doclaynet_a5c8842acf7bcf594695f43548ae05fdbf5e3938a6508b9bb3e99a87b8c454ec'
engine=RapidOCR(det_use_cuda=True,cls_use_cuda=True,rec_use_cuda=True,intra_op_num_threads=2,inter_op_num_threads=2)
img=np.asarray(Image.open(root/'dataset/images'/(ident+'.png')).convert('RGB'))[:,:,::-1].copy()
img,rh,rw=engine.preprocess(img);img,record=engine.maybe_add_letterbox(img,{'preprocess':{'ratio_h':rh,'ratio_w':rw}});boxes,_=engine.auto_text_det(img);crops=engine.get_crop_img_list(img,boxes)
before,_=engine.text_rec(copy.deepcopy(crops));rotated,angles,_=engine.text_cls(copy.deepcopy(crops));after,_=engine.text_rec(rotated)
rows=[]
for box,angle,a,b in zip(boxes,angles,before,after):
 if angle[0]=='180' and angle[1]>=engine.text_cls.cls_thresh:
  rows.append({'box':box.tolist(),'classification':{'angle':angle[0],'confidence':float(angle[1])},'without_rotation':{'text':a[0],'score':float(a[1])},'after_rotation':{'text':b[0],'score':float(b[1])},'dropped_after_rotation':a[1]>=.5 and b[1]<.5})
result={'id':ident,'detected_lines':len(boxes),'rotated_lines':len(rows),'dropped_after_rotation':sum(r['dropped_after_rotation'] for r in rows),'lines':rows}
(root/'direction-diagnosis.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n');print(json.dumps({k:v for k,v in result.items() if k!='lines'}))
