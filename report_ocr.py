#!/usr/bin/env python3
"""Build reproducible Markdown/CSV and browsable failure examples from scored results."""
import csv,hashlib,html,json
from datetime import datetime,timezone
from pathlib import Path
ROOT=Path(__file__).resolve().parent
BASE=ROOT/'results/real-ocr-v1'

def main():
    report=['# OCR 真实图片基线评测','',f'报告生成：{datetime.now(timezone.utc).isoformat()}','',
    '固定模型 rapidocr-gpu；并发 1、无自动重试；40 张校准集 + 160 张冻结评估集。调用本机 SSH 隧道，经 GPUStack 统一入口路由到两台 worker。未调整 OCR 模型或预处理。','',
    '| 集合 | 来源 | 成功/请求 | 归一化分组 CER | 严格分组 CER | 客户端 P50/P95 ms | 服务端 P50/P95 ms |',
    '|---|---|---:|---:|---:|---:|---:|']
    allrows=[]
    for split in ['calibration','evaluation']:
        summary=json.loads((BASE/split/'summary.json').read_text())
        for source,s in summary['sources'].items():
            report.append(f"| {split} | {source} | {s['success']}/{s['total']} | {s['normalized_grouped_cer']:.2%} | {s['strict_grouped_cer']:.2%} | {s['client_p50_ms']}/{s['client_p95_ms']} | {s['server_p50_ms']}/{s['server_p95_ms']} |")
        allrows.extend(json.loads(x) for x in (BASE/split/'scored.jsonl').read_text().splitlines())
    report+=['','## 指标口径与限制','',
    '- 按原图坐标匹配区域：交集面积 / 两框较小面积 ≥ 0.5 时建立连接；连通的参考框与预测框合为一组，以容纳拆行与合行。每组按近似水平阅读顺序拼接。未匹配参考计删除，未匹配预测计插入。',
    '- 优先匹配有效参考；其余预测若超过自身面积 50% 落在 ignore 区域则排除。零面积参考框单独计数并排除空间评分。',
    '- 严格 CER 保留区域文本空白、大小写及标点；归一化 CER 使用 Unicode NFKC、casefold、删除所有空白，保留标点。逐组编辑距离求和后除以参考字符总数，不对逐图 CER 取平均；插入多时 CER 可以超过 100%。',
    '- 这是自定义工程诊断指标，不是 RCTW 官方检测/识别比赛分数，也不是纯识别准确率。阅读顺序、多方向文字、参考框粒度仍会影响结果。未匹配区域数不是官方漏检率。',
    '- DocLayNet 参考来自 PDF 文本，存在符号映射及不可见文本问题；中文街景来自公开训练部分。没有对全部标注人工校正，不能据此承诺客户准确率。',
    '- 客户端耗时含网络、图片上传、网关和推理；服务端 elapsed_ms 主要包含 OCR 调用与锁等待，不含完整预处理/网络。此次顺序低并发调用不能代表双节点最大吞吐，也没有测量功耗。',
    '- calibration 用于检查评测流程；evaluation 使用相同冻结规则。模型配置未依据 evaluation 结果调整。','', '## 标注及路由审计','']
    for split in ['calibration','evaluation']:
        for source in ['doclaynet','rctw17']:
            rows=[r for r in allrows if r['split']==split and r['source']==source and r['ok']]
            counts={k:sum(r['metrics'][k] for r in rows) for k in ['invalid_reference_regions','reference_regions','missing_regions','unmatched_predictions','ignored_predictions']}
            nodes={}
            for r in rows:nodes[r['result']['node']]=nodes.get(r['result']['node'],0)+1
            report.append(f'- {split}/{source}: `{json.dumps(counts)}`；节点请求数 `{json.dumps(nodes)}`。')
    columns=['id','split','source','ok','client_ms','server_ms','node','normalized_cer','strict_cer','missing_regions','unmatched_predictions']
    with (BASE/'per-image.csv').open('w',encoding='utf-8-sig',newline='') as f:
        w=csv.DictWriter(f,fieldnames=columns);w.writeheader()
        for r in allrows:
            d={k:r[k] for k in ['id','split','source','ok','client_ms']}
            if r['ok']:
                m=r['metrics'];d.update(server_ms=r['result']['elapsed_ms'],node=r['result']['node'],normalized_cer=m['normalized_edits']/max(m['normalized_reference_chars'],1),strict_cer=m['strict_edits']/max(m['reference_chars'],1),missing_regions=m['missing_regions'],unmatched_predictions=m['unmatched_predictions'])
            w.writerow(d)
    report+=['','## 高错误样本（自动排序，需人工复核）','']
    page=['<!doctype html><meta charset="utf-8"><title>OCR 错误样本</title><style>body{font:16px sans-serif;max-width:1100px;margin:32px auto}img{max-width:100%;max-height:600px}pre{white-space:pre-wrap;overflow-wrap:anywhere;background:#f2f4f7;padding:16px}details{border-top:1px solid #bbb;padding:18px}</style><h1>OCR 高错误样本</h1><p>按每张图片归一化空间分组 CER 自动排序；参考文字及阅读顺序需人工核对。每类展示评估集前 5 张。绿色为有效参考框、灰色为忽略框、红色为预测框。坐标和图片均来自原始输入。</p>']
    manifest={r['id']:r for r in map(json.loads,(ROOT/'datasets/ocr-mixed-v1/manifest.jsonl').read_text().splitlines())}
    for source in ['doclaynet','rctw17']:
        ranked=sorted((r for r in allrows if r['source']==source and r['split']=='evaluation' and r['ok']),key=lambda r:r['metrics']['normalized_edits']/max(r['metrics']['normalized_reference_chars'],1),reverse=True)[:5]
        for r in ranked:
            ident=r['id'];m=r['metrics'];cer=m['normalized_edits']/max(m['normalized_reference_chars'],1);meta=manifest[ident];url='../../datasets/ocr-mixed-v1/'+meta['image']
            report.append(f"- [{ident}]({url})：分组 CER {cer:.1%}；未匹配参考区域 {m['missing_regions']}。")
            ann=json.loads((ROOT/'datasets/ocr-mixed-v1'/meta['annotation']).read_text())
            page.append(f'<details><summary>{ident} — CER {cer:.1%}</summary><svg viewBox="0 0 {meta["width"]} {meta["height"]}" style="max-height:700px;width:100%"><image href="{url}" width="{meta["width"]}" height="{meta["height"]}"/>')
            for points,color in [(x['polygon'],'gray' if x['ignore'] else 'green') for x in ann['regions']]+[(x['box'],'red') for x in r['result']['lines']]:
                page.append('<polygon fill="none" stroke="'+color+'" stroke-width="2" points="'+' '.join(f'{x},{y}' for x,y in points)+'"/>')
            page.append('</svg>')
            for g in sorted(m['groups'],key=lambda g:g['normalized_edits'],reverse=True)[:8]:page.append('<pre>'+html.escape('参考：'+g['reference']+'\n预测：'+g['prediction']+'\n编辑数：'+str(g['normalized_edits']))+'</pre>')
            page.append('</details>')
    (BASE/'failure-examples.html').write_text('\n'.join(page))
    report+=['','## 人工抽查发现','',
    '- 校准集英文财务页 a5c884…：原图中若干水平正文行没有出现在响应中，存在真实整行漏检/过滤问题；不能只看请求成功率。',
    '- 评估集英文 f883ed…：主要是旋转 90 度的小字；水平阅读顺序假设与 PDF 分块会额外抬高 CER，需要方向感知匹配或人工复核。',
    '- 评估集 RCTW image_3988：四个大字“用宫品前”正确识别；衣物上额外输出的拉丁字符串没有对应有效参考框，导致单图 CER 350%。这些未匹配输出可能包含背景误识别或未标注的真实文字，不能直接据此断言正文识别差。',
    '- 因此上述 CER 适合固定规则下比较版本，不能换算成“准确率 = 1 − CER”对外宣称。','']
    report+=['','## 后续动作','',
    '1. 人工核对错误样本页面，区分真实漏字、错字与参考/匹配问题。',
    '2. 优先在 40 张校准集上比较检测输入分辨率、低置信度过滤及分块处理对小字的影响；参数冻结后重新跑独立评估。',
    '3. 识别质量满足目标业务后，再做单节点/双节点并发 1、2、4、8 压测；随后开展节点排空和退出恢复实验。','',
    '文件：`per-image.csv` 逐图指标；`failure-examples.html` 原图与标注/预测框；各 split 下原始响应 JSON、`scored.jsonl`、`summary.json`。API key 不写入结果。']
    (BASE/'REPORT.md').write_text('\n'.join(report)+'\n')
    paths=[ROOT/'evaluate_ocr.py',ROOT/'report_ocr.py',ROOT/'datasets/ocr-mixed-v1/manifest.jsonl',ROOT/'datasets/ocr-mixed-v1/splits.json']
    (BASE/'reproducibility.json').write_text(json.dumps({'generated_at':datetime.now(timezone.utc).isoformat(),'files':{str(p.relative_to(ROOT)):hashlib.sha256(p.read_bytes()).hexdigest() for p in paths},'parameters':{'overlap_min_area_threshold':.5,'ignore_prediction_coverage_threshold':.5,'row_center_tolerance':.45,'normalization':'NFKC + casefold + remove whitespace; punctuation retained','concurrency':1,'retries':0}},indent=2)+'\n')
    print(BASE/'REPORT.md')
if __name__=='__main__':main()
