"""Compile variant comparison with paired uncertainty and a clear deployment decision."""
import collections,hashlib,json,random,sys
from datetime import datetime,timezone,timedelta
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];BASE=ROOT/'results/ocr-quality-v2'
LABELS={'A_default':'A 默认','B_resolution1536':'B 检测短边1536','C_tiles1024':'C 原图1024分块','D_score030':'D 文字阈值0.3','E_rotate90':'E 全图旋转90°'}
def ci_delta(base,candidate,iterations=2000):
    rng=random.Random(20261019);ids=sorted(set(base)&set(candidate));values=[]
    for _ in range(iterations):
        draw=rng.choices(ids,k=len(ids));den=sum(base[i]['metrics']['normalized_reference_chars'] for i in draw)
        delta=sum(candidate[i]['metrics']['normalized_edits']-base[i]['metrics']['normalized_edits'] for i in draw)/max(den,1)
        values.append(delta*100)
    values.sort();return [round(values[int((iterations-1)*p)],3) for p in [.025,.975]]
def main():
    summaries=json.loads((BASE/'summary.json').read_text());rows=list(map(json.loads,(BASE/'scored.jsonl').read_text().splitlines()))
    report=['# OCR 配置优化对比报告','',f'日期：{datetime.now(timezone(timedelta(hours=8))).isoformat()}（上海）','',
    '目的：在既有 GPUStack OCR 基线上，比较检测分辨率、分块、文字过滤及方向处理对识别质量与单图计算耗时的影响。所有实验使用 XZ-31-002 的独立无网络容器，线上服务配置保持不变。','',
    '## 结论','',
    '保留当前线上默认配置。840次计时图像处理全部成功（另有每组一次的预热）；默认组200张的文字及框结果与v1完全一致。',
    'B提高分辨率：英文评估CER由14.74%降到13.12%，平均耗时增加24%；但英文改善的配对95%区间跨零，且街景由18.47%退化到20.52%，尚不适合全局替换。',
    'C参考分块：英文CER降到10.13%，但平均耗时约4倍，且存在近乎重复窗口；街景退化到39.82%。仅可作为后续英文高精度模式的实验线索，不是已完成的生产方案。',
    'D降低过滤阈值：英文CER仅改善约0.09个百分点，街景退化到19.47%；E统一旋转90°在校准集明显退化，均不推荐直接启用。',
    '质量与成本仍需按客户业务图片独立验收。本轮不以增加输出量或减少未匹配框作为上线依据。','',
    '## 测试方法','',
    '- 固定 RapidOCR 1.4.4、PP-OCRv4 ONNX 模型、CUDA provider 与两线程配置；使用已部署 0.2 镜像。每组先预热一次，顺序逐图运行，并发为 1、无自动重试。',
    '- calibration：40 张（每来源20）；五组共200次。evaluation：160 张（每来源80）；A/B/C/D 四组共640次。E 在校准集明显退化，停止扩大。冻结计划见 frozen-plan.json。',
    '- 测量 inference elapsed_ms：从图片解码完成后开始，包含预处理、模型调用、旋转/分块及合并；不含网络、网关、文件读取、模型初始化。与上轮客户端延迟不可直接比较。',
    '- 质量指标沿用 v1 空间连通分组 CER：框交集/较小框面积≥0.5建立匹配；忽略区域按预测覆盖处理；NFKC + casefold + 去空白，保留标点。全部规则固定，指标程序未按评估集调参。',
    '- DocLayNet 是 PDF 文字参考；RCTW 是公开训练部分。公式、旋转、多栏阅读顺序、未标注背景文字会影响数值。此 CER 不等于纯识别错误率，也不能用 1−CER 宣称商业准确率。evaluation 在上轮已经查看过，属于版本回归验证，不是新的盲测。','',
    '| 方案 | 相对默认的唯一处理变化 |', '|---|---|',
    '| A | RapidOCR 默认：检测短边下限736、全图预处理长边上限2000、文字置信度0.5 |',
    '| B | 仅检测短边下限改为1536，其他保持默认；注意不是所有图像都会按同一倍率放大 |',
    '| C | 原图1024×1024滑窗，重叠192，边界窗口锚定末端；每窗用默认引擎，将坐标映回原图后按空间和文字相似性去重 |',
    '| D | 仅识别结果文字过滤阈值由0.5降到0.3，不改变检测阈值 |',
    '| E | 整图逆时针旋转90°后识别，坐标映回原图；用于方向诊断，没有自动方向选择 |','']
    for split,title in [('calibration','校准集'),('evaluation','评估集')]:
        report+=['## '+title,'','| 方案 | 来源 | 成功/总数 | 归一化分组CER | P50 / P95 ms | 平均耗时相对A | 未匹配参考区域 |','|---|---|---:|---:|---:|---:|---:|']
        for variant in LABELS:
            key=variant+'/'+split
            if key not in summaries:continue
            for source,s in summaries[key].items():
                baseline=summaries['A_default/'+split][source]
                report.append(f"| {LABELS[variant]} | {source} | {s['success']}/{s['total']} | {s['normalized_cer']:.2%} | {s['inference_p50_ms']}/{s['inference_p95_ms']} | {s['mean_ms']/baseline['mean_ms']:.2f}× | {s['counts']['missing_regions']}/{s['counts']['reference_regions']} |")
        report.append('')
    comparisons={};report+=['## 评估集上的成对比较','','以下 CER 差值单位为百分点；负数更好。区间为按图片配对重抽样2000次得到的95% bootstrap区间，只反映本批样本的不确定性。','', '| 方案 | 来源 | CER变化百分点 | 95%区间 | 改善/持平/退化图片数 |','|---|---|---:|---:|---:|']
    for variant in ['B_resolution1536','C_tiles1024','D_score030']:
        for source in ['doclaynet','rctw17']:
            a={r['id']:r for r in rows if r['variant']=='A_default' and r['split']=='evaluation' and r['source']==source and r['ok']};b={r['id']:r for r in rows if r['variant']==variant and r['split']=='evaluation' and r['source']==source and r['ok']}
            if len(a)!=80 or len(b)!=80:raise ValueError('Incomplete evaluation: '+variant+'/'+source)
            outcomes=collections.Counter('better' if b[i]['metrics']['normalized_edits']<a[i]['metrics']['normalized_edits'] else 'worse' if b[i]['metrics']['normalized_edits']>a[i]['metrics']['normalized_edits'] else 'same' for i in a)
            delta=(summaries[variant+'/evaluation'][source]['normalized_cer']-summaries['A_default/evaluation'][source]['normalized_cer'])*100;ci=ci_delta(a,b);comparisons[variant+'/'+source]={'delta_pp':delta,'bootstrap_95_pp':ci,'paired_counts':dict(outcomes)}
            report.append(f"| {LABELS[variant]} | {source} | {delta:+.2f} | [{ci[0]:+.2f}, {ci[1]:+.2f}] | {outcomes['better']}/{outcomes['same']}/{outcomes['worse']} |")
    (BASE/'paired-comparisons.json').write_text(json.dumps(comparisons,indent=2)+'\n')
    report+=['','## 编辑数分解（评估集）','',
    '以下为固定分组和阅读顺序下的替换/删除/插入字符数；它们也受标注完整性及分组影响。','',
    '| 方案 | 来源 | 替换 | 删除 | 插入 |', '|---|---|---:|---:|---:|']
    for variant in ['A_default','B_resolution1536','C_tiles1024','D_score030']:
        for source,s in summaries[variant+'/evaluation'].items():
            c=s['counts'];report.append(f"| {LABELS[variant]} | {source} | {c['normalized_substitutions']} | {c['normalized_deletions']} | {c['normalized_insertions']} |")
    report+=['','C在街景中插入数由79升至486，说明分块输出增加伴随额外/重复/未标注文本问题；不能用减少未匹配参考框证明效果改善。','',
    '## 错误类型与复核材料','',
    '已按基线绝对编辑数选择每来源6张，共12张校准图片，生成 comparison.html；可逐配置打开原图与预测框，并查看高编辑数组的参考/预测文字。review-ids.json 固定样本列表，review-sheet-1/2.jpg 为联系表。人工复核结论见 REVIEW.md。','',
    '- 密集英文正文存在整行漏字；提高分辨率可救回部分行，但同时可能改变合框或识别结果。',
    '- 论文公式含 PDF 转义/上下标等参考格式，部分编辑数不属于普通正文识别问题。',
    '- 街景的远处小字、英文副标题和商标比较困难；降低过滤阈值能增加输出，但也会增加不可靠文字。',
    '- C 是参考分块实现，未做生产级拼接。尤其1025×1025的DocLayNet原图会产生4个近乎重叠的1024窗口，相当于多次偏移裁剪融合；其改善不能简单归因于提高有效分辨率。重复窗口明显抬高计算成本，仍可能有截断/残余重复。','',
    '## 部署决策与下一步','',
    '本轮不自动替换线上默认配置。校准集没有出现中英文同时明显改善且成本合理的方案；评估集对照用于确认各方案的适用范围，具体结论见开头摘要及成对比较表。',
    '下一轮应将普通水平文档与街景分开验收，补充接近客户输入的新图片及人工校正参考；只有确认质量收益后，再测试更换模型或面向小字的处理策略。需要高精度模式时，应单独评估分块收益，并先消除近乎重复窗口。',
    '计算耗时倍率只是计算成本代理；本轮没有测量整机功耗、并发吞吐、GPUStack调度容量或每千张实际费用。不能将单图耗时直接换算成最终报价。','',
    '## 文件与复现','',
    '- summary.json：来源分组汇总，含严格/归一化 CER、替换/删除/插入数、窗口调用数和去重数。',
    '- per-image.csv / scored.jsonl：逐图指标与参考/预测组；raw/：全部原始推理响应和运行参数。',
    '- paired-comparisons.json：配对比较及bootstrap区间。',
    '- frozen-plan.json、reproducibility.json、variant-tests.log、RUN.md：冻结计划、代码校验和、测试及运行说明。',
    '- 脚本位于 wtaobj/ocr-experiments/，线上适配层源码未修改。']
    (BASE/'REPORT.md').write_text('\n'.join(report)+'\n')
    paths=[ROOT/'ocr-experiments'/n for n in ['run_variants.py','analyze.py','make_report.py','test_variants.py']]+[ROOT/'evaluate_ocr.py',ROOT/'datasets/ocr-mixed-v1/splits.json',ROOT/'datasets/ocr-mixed-v1/manifest.jsonl']
    (BASE/'reproducibility.json').write_text(json.dumps({str(p.relative_to(ROOT)):hashlib.sha256(p.read_bytes()).hexdigest() for p in paths},indent=2)+'\n')
    print(BASE/'REPORT.md')
if __name__=='__main__':main()
