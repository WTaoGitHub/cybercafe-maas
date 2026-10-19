#!/usr/bin/env python3
"""Sequential OCR baseline; spatially grouped CER is diagnostic, not official scoring."""
import argparse,base64,collections,hashlib,json,mimetypes,time,unicodedata,urllib.request
from pathlib import Path
from shapely.geometry import Polygon
from rapidfuzz.distance import Levenshtein
ROOT=Path(__file__).resolve().parent

def normalize(s):
    return ''.join(c for c in unicodedata.normalize('NFKC',s).casefold() if not c.isspace())

def poly(points):
    p=Polygon(points)
    return p if p.is_valid else p.buffer(0)

def ordered(items):
    # Approximate horizontal reading order; vertical/complex layouts require manual review.
    rows=[]
    for p,t in sorted(items,key=lambda x:x[0].centroid.y):
        y=p.centroid.y; h=p.bounds[3]-p.bounds[1]
        row=next((r for r in rows if abs(r[0]-y)<=.45*min(r[1],h)),None)
        if row is None: rows.append([y,h,[(p,t)]])
        else: row[2].append((p,t))
    return ''.join(t for r in rows for p,t in sorted(r[2],key=lambda x:x[0].centroid.x))

def score(annotation,result):
    raw_gt=[(poly(r['polygon']),r['text']) for r in annotation['regions'] if not r['ignore']]
    gt=[x for x in raw_gt if not x[0].is_empty and x[0].area>0]
    ignored=[poly(r['polygon']) for r in annotation['regions'] if r['ignore']]
    pred=[(poly(r['box']),r['text']) for r in result['lines']]
    # Valid reference takes priority over overlapping ignored regions.
    edges=[]; excluded=[]
    for j,(p,t) in enumerate(pred):
        hits=[i for i,(g,_) in enumerate(gt) if p.intersection(g).area/max(min(p.area,g.area),1e-9)>=.5]
        if hits: edges.extend((i,len(gt)+j) for i in hits)
        elif any(p.intersection(g).area/max(p.area,1e-9)>.5 for g in ignored): excluded.append(j)
    parent=list(range(len(gt)+len(pred)))
    def find(i):
        while parent[i]!=i: parent[i]=parent[parent[i]];i=parent[i]
        return i
    for i,j in edges:parent[find(j)]=find(i)
    groups=collections.defaultdict(lambda:[[],[]])
    for i,x in enumerate(gt):groups[find(i)][0].append(x)
    for j,x in enumerate(pred):
        if j not in excluded:groups[find(len(gt)+j)][1].append(x)
    out={'invalid_reference_regions':len(raw_gt)-len(gt),'reference_chars':0,'strict_edits':0,'normalized_reference_chars':0,'normalized_edits':0,'missing_regions':sum(not any(i==a for a,b in edges) for i in range(len(gt))),'reference_regions':len(gt),'unmatched_predictions':0,'ignored_predictions':len(excluded),'groups':[]}
    for refs,preds in groups.values():
        a,b=ordered(refs),ordered(preds);na,nb=normalize(a),normalize(b)
        d=Levenshtein.distance(a,b);nd=Levenshtein.distance(na,nb)
        out['reference_chars']+=len(a);out['strict_edits']+=d
        out['normalized_reference_chars']+=len(na);out['normalized_edits']+=nd
        if not refs:out['unmatched_predictions']+=len(preds)
        out['groups'].append({'reference':a,'prediction':b,'normalized_edits':nd,'reference_regions':len(refs),'predicted_regions':len(preds)})
    return out

def percentile(values,p):
    v=sorted(values);x=(len(v)-1)*p;i=int(x)
    return round(v[i]+(v[min(i+1,len(v)-1)]-v[i])*(x-i),2) if v else None

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--split',choices=['calibration','evaluation'],required=True);ap.add_argument('--base-url',default='http://127.0.0.1:18081');ap.add_argument('--output',type=Path,default=ROOT/'results/real-ocr-v1');a=ap.parse_args()
    dataset=ROOT/'datasets/ocr-mixed-v1';splits=json.loads((dataset/'splits.json').read_text());manifest={r['id']:r for r in map(json.loads,(dataset/'manifest.jsonl').read_text().splitlines())}
    dest=a.output/a.split;dest.mkdir(parents=True,exist_ok=True)
    key=(ROOT/'.gpustack-api-key').read_text().strip();rows=[]
    for n,ident in enumerate(splits[a.split],1):
        path=dest/(ident+'.json');m=manifest[ident]
        if path.exists():row=json.loads(path.read_text())
        else:
            raw=(dataset/m['image']).read_bytes();assert hashlib.sha256(raw).hexdigest()==m['sha256']
            body={'model':'rapidocr-gpu','stream':False,'messages':[{'role':'user','content':[{'type':'image_url','image_url':{'url':'data:'+mimetypes.guess_type(m['image'])[0]+';base64,'+base64.b64encode(raw).decode()}}]}]}
            start=time.perf_counter();row={'id':ident,'source':m['source'],'split':a.split,'image_sha256':m['sha256']}
            try:
                req=urllib.request.Request(a.base_url+'/v1/chat/completions',data=json.dumps(body).encode(),headers={'Authorization':'Bearer '+key,'Content-Type':'application/json'})
                with urllib.request.urlopen(req,timeout=120) as response:res=json.load(response)
                row['client_ms']=round((time.perf_counter()-start)*1000,2);row['result']=json.loads(res['choices'][0]['message']['content']);row['ok']=True
            except Exception as e:row.update(ok=False,error=type(e).__name__+': '+str(e),client_ms=round((time.perf_counter()-start)*1000,2))
            path.write_text(json.dumps(row,ensure_ascii=False,indent=2)+'\n')
        if row['ok']:row['metrics']=score(json.loads((dataset/m['annotation']).read_text()),row['result'])
        rows.append(row);print(f"{a.split} {n}/{len(splits[a.split])} {ident[:28]} ok={row['ok']}",flush=True)
    (dest/'scored.jsonl').write_text(''.join(json.dumps(r,ensure_ascii=False)+'\n' for r in rows))
    summary={'split':a.split,'concurrency':1,'retries':0,'model':'rapidocr-gpu','sources':{}}
    for source in ['doclaynet','rctw17']:
        allrows=[r for r in rows if r['source']==source];good=[r for r in allrows if r['ok']];tot=collections.Counter()
        for r in good:tot.update({k:v for k,v in r['metrics'].items() if isinstance(v,int)})
        summary['sources'][source]={'total':len(allrows),'success':len(good),'client_p50_ms':percentile([r['client_ms'] for r in good],.5),'client_p95_ms':percentile([r['client_ms'] for r in good],.95),'server_p50_ms':percentile([r['result']['elapsed_ms'] for r in good],.5),'server_p95_ms':percentile([r['result']['elapsed_ms'] for r in good],.95),'strict_grouped_cer':tot['strict_edits']/max(tot['reference_chars'],1),'normalized_grouped_cer':tot['normalized_edits']/max(tot['normalized_reference_chars'],1),'counts':dict(tot),'nodes':dict(collections.Counter(r['result']['node'] for r in good))}
    (dest/'summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2)+'\n');print(json.dumps(summary,ensure_ascii=False,indent=2))
if __name__=='__main__':main()
