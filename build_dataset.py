"""Build reproducible full-image OCR evaluation corpus with source-level labels."""
import hashlib,json,random,re,shutil
from pathlib import Path
from PIL import Image,ImageDraw
from langdetect import detect,DetectorFactory
DetectorFactory.seed=20261016
ROOT=Path(__file__).resolve().parent/'datasets'; OUT=ROOT/'ocr-mixed-v1'

def save(source, original, image, regions, metadata, language, gt_type):
    data=image.read_bytes();sha=hashlib.sha256(data).hexdigest()
    ident=source+'_'+original
    dest=OUT/'images'/(ident+image.suffix.lower());shutil.copyfile(image,dest)
    # Simple y/x order is a convenience transcript, not a reading-order gold standard.
    valid=[r for r in regions if not r.get('ignore') and r['text'].strip()]
    valid.sort(key=lambda r:(min(p[1] for p in r['polygon']),min(p[0] for p in r['polygon'])))
    text='\n'.join(r['text'] for r in valid)
    (OUT/'ground_truth'/(ident+'.txt')).write_text(text+'\n',encoding='utf-8')
    annotation={'id':ident,'regions':regions,'reading_order':'approximate_y_x','reference_type':gt_type,'metadata':metadata}
    (OUT/'annotations'/(ident+'.json')).write_text(json.dumps(annotation,ensure_ascii=False,indent=2),encoding='utf-8')
    with Image.open(image) as im:w,h=im.size
    return {'id':ident,'source':source,'original_id':original,'image':str(dest.relative_to(OUT)),
        'ground_truth':f'ground_truth/{ident}.txt','annotation':f'annotations/{ident}.json',
        'language':language,'width':w,'height':h,'bytes':len(data),'sha256':sha,
        'characters':len(re.sub(r'\s','',text)),'regions':len(valid),'ignored_regions':len(regions)-len(valid),
        'reference_type':gt_type,'commercial_permission':'CDLA-Permissive-1.0' if source=='doclaynet' else 'unconfirmed'}

def build():
    for p in ('images','ground_truth','annotations'): (OUT/p).mkdir(parents=True,exist_ok=True)
    rows=[]
    files=sorted((ROOT/'sources/doclaynet/annotations').glob('*.json'))
    random.Random(20261016).shuffle(files)
    seen_docs=set()
    for file in files:
        d=json.loads(file.read_text());text=' '.join(c['text'] for c in d['cells'])
        if len(text)<100:continue
        try:lang=detect(text)
        except Exception:continue
        if lang!='en':continue
        doc=d['metadata']['original_filename']
        if doc in seen_docs:continue
        seen_docs.add(doc)
        regions=[]
        for cell in d['cells']:
            x,y,w,h=cell['bbox'];regions.append({'text':cell['text'],'polygon':[[x,y],[x+w,y],[x+w,y+h],[x,y+h]],'ignore':False})
        rows.append(save('doclaynet',file.stem,ROOT/'sources/doclaynet/images'/(file.stem+'.png'),regions,d['metadata'],'en_auto_detected','PDF digital text; not manually corrected OCR gold'))
        if len(rows)==100:break
    doc_count=len(rows)
    candidates=sorted((ROOT/'sources/rctw17/annotations').glob('*.json'));random.Random(20261016).shuffle(candidates)
    for file in candidates:
        d=json.loads(file.read_text());image=ROOT/'sources/rctw17/images'/(file.stem+'.jpg')
        regions=[]
        for item in d['instances']:
            regions.append({'text':item['text'],'polygon':item['polygon'][0],'ignore':bool(item['difficult']) or item['text']=='###'})
        if not any(re.search('[\u4e00-\u9fff]',r['text']) for r in regions if not r['ignore']):continue
        rows.append(save('rctw17',file.stem,image,regions,{'original_path':d['image']['img_path'],'image_transport':d.get('image_transport')},'zh_mixed','RCTW-17 annotations redistributed as OpenDataLab DSDL'))
        if len(rows)-doc_count==100:break
    random.Random(20261016).shuffle(rows)
    (OUT/'manifest.jsonl').write_text(''.join(json.dumps(r,ensure_ascii=False)+'\n' for r in rows))
    # Explicitly freeze calibration vs evaluation; never tune on evaluation results.
    calibration=[];evaluation=[]
    for source in ('doclaynet','rctw17'):
        subset=[r['id'] for r in rows if r['source']==source]
        calibration+=subset[:20];evaluation+=subset[20:]
    (OUT/'splits.json').write_text(json.dumps({'seed':20261016,'calibration':calibration,'evaluation':evaluation},indent=2))
    summary={'total':len(rows),'doclaynet':doc_count,'rctw17':len(rows)-doc_count,'seed':20261016,
             'calibration':len(calibration),'evaluation':len(evaluation),'unique_images':len(set(r['sha256'] for r in rows))}
    (OUT/'summary.json').write_text(json.dumps(summary,indent=2))
    preview=Image.new('RGB',(1000,1000),'#ddd');draw=ImageDraw.Draw(preview)
    chosen=[r for s in ('doclaynet','rctw17') for r in rows if r['source']==s][:0]
    chosen=[r for r in rows if r['source']=='doclaynet'][:8]+[r for r in rows if r['source']=='rctw17'][:8]
    for i,r in enumerate(chosen):
        with Image.open(OUT/r['image']) as im:
            im=im.convert('RGB');im.thumbnail((240,220));x=(i%4)*250;y=(i//4)*250
            preview.paste(im,(x+(250-im.width)//2,y));draw.text((x+5,y+225),r['source']+' '+r['original_id'][:12],fill='black')
    preview.save(OUT/'preview.jpg')
    print(json.dumps(summary))
if __name__=='__main__':build()
