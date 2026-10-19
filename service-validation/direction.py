"""Rotate original pixel arrays losslessly; compare directions in original coordinates."""
import argparse,hashlib,json,random,sys,time
from pathlib import Path
sys.path.insert(0,'/app')
import numpy as np
from PIL import Image
from engine import Engine,rotate_image,rotate_points,rotated_size
p=argparse.ArgumentParser();p.add_argument('--dataset',type=Path,default=Path('/experiment/dataset'));p.add_argument('--output',type=Path,default=Path('/experiment/service-direction'));a=p.parse_args()
manifest={r['id']:r for r in map(json.loads,(a.dataset/'manifest.jsonl').read_text().splitlines())};splits=json.loads((a.dataset/'splits.json').read_text());selected=[]
for split,count in [('calibration',6),('evaluation',10)]:
 for source in ['doclaynet','rctw17']:
  choices=[x for x in splits[split] if manifest[x]['source']==source];random.Random(20261019).shuffle(choices);selected.extend((split,x) for x in choices[:count])
a.output.mkdir(parents=True,exist_ok=True);(a.output/'plan.json').write_text(json.dumps({'selected':selected,'angles_cw':[0,90,180,270],'variants':['v4_legacy','v4_upright','v4_guarded','v6_small'],'explicit_correction':['v4_upright','v6_small'],'note':'Lossless array rotation; predictions transformed back to original coordinates for unchanged spatial scoring; no ground-truth orientation inferred by the model.'},indent=2)+'\n')
engine=Engine();first=manifest[selected[0][1]];array=np.asarray(Image.open(a.dataset/first['image']).convert('RGB'))[:,:,::-1].copy()
for mode in ('v4_legacy','v4_upright','v4_guarded','v6_small'):engine.run(array,mode)
def run_case(split,ident,original,angle,variant,correction):
 label='corrected' if correction else 'as_is';dest=a.output/variant/label/split;dest.mkdir(parents=True,exist_ok=True);path=dest/(ident+f'-{angle:03d}.json')
 if path.exists():return
 observed=rotate_image(original,angle);rotation=(-angle)%360 if correction else 0
 start=time.perf_counter();result=engine.run(observed,variant,rotation);ms=(time.perf_counter()-start)*1000
 height,width=original.shape[:2];ow,oh=rotated_size(width,height,angle)
 for line in result['lines']:line['box']=rotate_points(line['box'],ow,oh,(-angle)%360).tolist()
 result['elapsed_ms']=round(ms,2)
 row={'id':ident,'source':manifest[ident]['source'],'split':split,'input_rotation_cw':angle,'explicit_correction':correction,'variant':variant,'image_sha256':manifest[ident]['sha256'],'ok':True,'score_coordinate_frame':'original','result':result}
 path.write_text(json.dumps(row,ensure_ascii=False,indent=2)+'\n')
for index,(split,ident) in enumerate(selected,1):
 rec=manifest[ident];data=(a.dataset/rec['image']).read_bytes();assert hashlib.sha256(data).hexdigest()==rec['sha256']
 original=np.asarray(Image.open(a.dataset/rec['image']).convert('RGB'))[:,:,::-1].copy()
 for angle in (0,90,180,270):
  for variant in ('v4_legacy','v4_upright','v4_guarded','v6_small'):run_case(split,ident,original,angle,variant,False)
  if angle:
   for variant in ('v4_upright','v6_small'):run_case(split,ident,original,angle,variant,True)
 print('direction',index,len(selected),ident[:30],flush=True)
# Evaluate guarded mode on all 200 upright originals; no retuning after rotation results.
for split in ('calibration','evaluation'):
 for index,ident in enumerate(splits[split],1):
  if (split,ident) in selected:continue
  original=np.asarray(Image.open(a.dataset/manifest[ident]['image']).convert('RGB'))[:,:,::-1].copy();run_case(split,ident,original,0,'v4_guarded',False)
  print('guarded full',split,index,len(splits[split]),flush=True)
print('direction and guarded full-set validation complete',flush=True)
