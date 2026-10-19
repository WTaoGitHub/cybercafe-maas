"""Score isolated variants with the frozen v1 spatial metric."""
import collections,csv,hashlib,html,json,sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from evaluate_ocr import score,percentile,normalize
from rapidfuzz.distance import Levenshtein
ROOT=Path(__file__).resolve().parents[1]
BASE=ROOT/'results/ocr-model-v3'
DATA=ROOT/'datasets/ocr-mixed-v1'

def main():
    allrows=[];summaries={};cache={}
    # Reuse only identical raw responses scored by the identical frozen metric.
    previous=BASE/'reproducibility.json'
    if previous.exists() and (BASE/'scored.jsonl').exists():
        rules=json.loads(previous.read_text())
        if rules.get('evaluate_ocr.py')==hashlib.sha256((ROOT/'evaluate_ocr.py').read_bytes()).hexdigest():
            cache={(r['variant'],r['split'],r['id']):r for r in map(json.loads,(BASE/'scored.jsonl').read_text().splitlines())}

    for variantpath in sorted((BASE/'raw').iterdir()):
        if not variantpath.is_dir():continue
        for splitpath in sorted(variantpath.iterdir()):
            if not splitpath.is_dir():continue
            rows=[]
            for f in sorted(splitpath.glob('*.json')):
                if f.name=='run-info.json':continue
                r=json.loads(f.read_text())
                if r['ok']:
                    cached=cache.get((r['variant'],r['split'],r['id']))
                    if cached and cached.get('result')==r['result'] and cached.get('source')==r['source']:
                        r['metrics']=cached['metrics']
                    else:
                        r['metrics']=score(json.loads((DATA/'annotations'/(r['id']+'.json')).read_text()),r['result'])
                    counts=collections.Counter(op.tag for g in r['metrics']['groups'] for op in Levenshtein.editops(normalize(g['reference']),normalize(g['prediction'])))
                    r['metrics'].update(normalized_substitutions=counts['replace'],normalized_deletions=counts['delete'],normalized_insertions=counts['insert'])
                rows.append(r)
            if not rows:continue
            summary={}
            for source in ['doclaynet','rctw17']:
                rs=[r for r in rows if r['source']==source];good=[r for r in rs if r['ok']];tot=collections.Counter()
                for r in good:tot.update({k:v for k,v in r['metrics'].items() if isinstance(v,int)})
                summary[source]={'total':len(rs),'success':len(good),'normalized_cer':tot['normalized_edits']/max(tot['normalized_reference_chars'],1),'strict_cer':tot['strict_edits']/max(tot['reference_chars'],1),'inference_p50_ms':percentile([r['result']['elapsed_ms'] for r in good],.5),'inference_p95_ms':percentile([r['result']['elapsed_ms'] for r in good],.95),'mean_ms':round(sum(r['result']['elapsed_ms'] for r in good)/max(len(good),1),2),'counts':dict(tot),'engine_calls':sum(r['result']['engine_calls'] for r in good),'deduplicated':sum(r['result']['deduplicated'] for r in good)}
            summaries[variantpath.name+'/'+splitpath.name]=summary;allrows+=rows
    (BASE/'summary.json').write_text(json.dumps(summaries,ensure_ascii=False,indent=2)+'\n')
    (BASE/'scored.jsonl').write_text(''.join(json.dumps(r,ensure_ascii=False)+'\n' for r in allrows))
    fields=['variant','split','id','source','ok','inference_ms','normalized_cer','missing_regions','unmatched_predictions']
    with (BASE/'per-image.csv').open('w',encoding='utf-8-sig',newline='') as f:
        out=csv.DictWriter(f,fieldnames=fields);out.writeheader()
        for r in allrows:
            d={k:r[k] for k in fields[:5]}
            if r['ok']:
                m=r['metrics'];d.update(inference_ms=r['result']['elapsed_ms'],normalized_cer=m['normalized_edits']/max(m['normalized_reference_chars'],1),missing_regions=m['missing_regions'],unmatched_predictions=m['unmatched_predictions'])
            out.writerow(d)
    print('variant/split source n CER p50/p95 mean_ms missing')
    for key,ss in summaries.items():
        for source,s in ss.items():print(key,source,s['success'],round(s['normalized_cer']*100,2),s['inference_p50_ms'],s['inference_p95_ms'],s['mean_ms'],s['counts'].get('missing_regions',0))
    # Audit cases are selected only from calibration, by absolute baseline edit count per source.
    refs={r['id']:r for r in allrows if r['variant']=='A_default' and r['split']=='calibration' and r['ok']}
    selected=[]
    for source in ['doclaynet','rctw17']:
        selected+=sorted((r for r in refs.values() if r['source']==source),key=lambda r:r['metrics']['normalized_edits'],reverse=True)[:6]
    manifest={r['id']:r for r in map(json.loads,(DATA/'manifest.jsonl').read_text().splitlines())}
    page=['<!doctype html><meta charset="utf-8"><title>OCR 模型对比</title><style>body{font:15px sans-serif;max-width:1300px;margin:32px auto}pre{white-space:pre-wrap;overflow-wrap:anywhere;background:#f4f6f8;padding:12px}svg{width:100%;max-height:750px}details{border-top:1px solid #ccc;padding:20px}table{border-collapse:collapse}td,th{padding:8px;border:1px solid #ccc}</style><h1>12 张校准图片配置对比</h1><p>每来源按基线绝对编辑数选择前 6 张。原始预测全部保留；蓝色为参考、灰色为忽略、红色为预测。空间分组指标受阅读顺序与标注影响，不等于纯识别准确率。</p>']
    for ref in selected:
        ident=ref['id'];meta=manifest[ident];ann=json.loads((DATA/meta['annotation']).read_text())
        page.append(f'<h2>{ident}</h2>')
        for r in [r for r in allrows if r['split']=='calibration' and r['id']==ident and r['ok']]:
            m=r['metrics'];cer=m['normalized_edits']/max(m['normalized_reference_chars'],1)
            page.append(f'<details><summary>{r["variant"]}: CER {cer:.1%}；{r["result"]["elapsed_ms"]} ms；未匹配参考 {m["missing_regions"]}</summary><svg viewBox="0 0 {meta["width"]} {meta["height"]}"><image href="../../datasets/ocr-mixed-v1/{meta["image"]}" width="{meta["width"]}" height="{meta["height"]}"/>')
            for points,color in [(x['polygon'],'gray' if x['ignore'] else 'blue') for x in ann['regions']]+[(x['box'],'red') for x in r['result']['lines']]:page.append('<polygon fill="none" stroke="'+color+'" stroke-width="2" points="'+' '.join(f'{x},{y}' for x,y in points)+'"/>')
            page.append('</svg>')
            for g in sorted(m['groups'],key=lambda g:g['normalized_edits'],reverse=True)[:8]:page.append('<pre>'+html.escape('参考：'+g['reference']+'\n预测：'+g['prediction']+'\n编辑数：'+str(g['normalized_edits']))+'</pre>')
            page.append('</details>')
    (BASE/'comparison.html').write_text('\n'.join(page))
    (BASE/'review-ids.json').write_text(json.dumps([r['id'] for r in selected],indent=2)+'\n')
if __name__=='__main__':main()
