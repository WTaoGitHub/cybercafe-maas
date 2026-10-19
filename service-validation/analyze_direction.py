"""Score rotation experiments in the original, fixed ground-truth coordinate frame."""
import collections,hashlib,json,sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from evaluate_ocr import score
ROOT=Path(__file__).resolve().parents[1];BASE=ROOT/'results/ocr-service-v4';DATA=ROOT/'datasets/ocr-mixed-v1'
def summarize(rows):
 out={}
 for source in ['doclaynet','rctw17']:
  rs=[r for r in rows if r['source']==source];counts=collections.Counter()
  for r in rs:counts.update({k:v for k,v in r['metrics'].items() if isinstance(v,int)})
  out[source]={'images':len(rs),'normalized_cer':counts['normalized_edits']/max(counts['normalized_reference_chars'],1),'counts':dict(counts),'mean_processing_ms':sum(r['result']['processing_ms'] for r in rs)/max(len(rs),1)}
 return out
plan=json.loads((BASE/'plan.json').read_text());selected={x[1] for x in plan['selected']};rows=[]
for mode in ['v4_legacy','v4_upright','v4_guarded','v6_small']:
 for f in (BASE/mode).rglob('*.json'):
  r=json.loads(f.read_text());assert r['ok'];r['metrics']=score(json.loads((DATA/'annotations'/(r['id']+'.json')).read_text()),r['result']);rows.append(r)
assert len(rows)==872,len(rows)
summary={'rotation_subset':{},'explicit_correction':{},'full_guarded_upright':{},'explicit_pixel_restore_text_mismatches':[]}
for mode in ['v4_legacy','v4_upright','v4_guarded','v6_small']:
 summary['rotation_subset'][mode]={}
 for angle in [0,90,180,270]:summary['rotation_subset'][mode][str(angle)]=summarize([r for r in rows if r['variant']==mode and not r['explicit_correction'] and r['input_rotation_cw']==angle and r['id'] in selected])
for mode in ['v4_upright','v6_small']:
 summary['explicit_correction'][mode]={}
 baseline={r['id']:r for r in rows if r['variant']==mode and not r['explicit_correction'] and r['input_rotation_cw']==0 and r['id'] in selected}
 for angle in [90,180,270]:
  rs=[r for r in rows if r['variant']==mode and r['explicit_correction'] and r['input_rotation_cw']==angle];assert len(rs)==32
  summary['explicit_correction'][mode][str(angle)]=summarize(rs)
  for r in rs:
   if r['result']['text']!=baseline[r['id']]['result']['text']:summary['explicit_pixel_restore_text_mismatches'].append({'variant':mode,'angle':angle,'id':r['id']})
for split in ['calibration','evaluation']:
 rs=[r for r in rows if r['variant']=='v4_guarded' and r['input_rotation_cw']==0 and not r['explicit_correction'] and r['split']==split];assert len(rs)==(40 if split=='calibration' else 160)
 summary['full_guarded_upright'][split]=summarize(rs)
(BASE/'direction-scored.jsonl').write_text(''.join(json.dumps(r,ensure_ascii=False)+'\n' for r in rows));(BASE/'direction-summary.json').write_text(json.dumps(summary,indent=2)+'\n')
print('Scored',len(rows),'image runs; explicit correction mismatches',len(summary['explicit_pixel_restore_text_mismatches']))
print('Guarded full evaluation',json.dumps(summary['full_guarded_upright']['evaluation']))
for mode,angles in summary['rotation_subset'].items():
 print(mode,{angle:{s:round(v['normalized_cer']*100,2) for s,v in data.items()} for angle,data in angles.items()})
