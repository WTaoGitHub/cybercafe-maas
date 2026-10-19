"""Report model quality/latency/memory, including a decision on a VLM trial."""
import collections,hashlib,json,random
from datetime import datetime,timedelta,timezone
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];BASE=ROOT/'results/ocr-model-v3'
LABELS={'A_default':'旧 PP-OCRv4（原方向分类）','A_v4_no_cls':'旧 PP-OCRv4（关闭方向分类）','M1_v6_small':'PP-OCRv6 small','M2_v6_medium':'PP-OCRv6 medium','M3_v5_server':'PP-OCRv5 server'}
def paired_ci(a,b):
    rng=random.Random(20261019);ids=sorted(a);values=[]
    for _ in range(2000):
        draw=rng.choices(ids,k=len(ids));den=sum(a[i]['metrics']['normalized_reference_chars'] for i in draw)
        values.append(100*sum(b[i]['metrics']['normalized_edits']-a[i]['metrics']['normalized_edits'] for i in draw)/max(den,1))
    values.sort();return [values[int(1999*p)] for p in [.025,.975]]
def main():
    ss=json.loads((BASE/'summary.json').read_text());rows=list(map(json.loads,(BASE/'scored.jsonl').read_text().splitlines()))
    text=['# OCR 模型对比报告','',f'生成时间：{datetime.now(timezone(timedelta(hours=8))).isoformat()}（上海）','',
    '目标：测试 PP-OCRv6 small、PP-OCRv6 medium、PP-OCRv5 server，比较中英文普通文字提取的质量与计算成本，再评估是否值得扩大到 PaddleOCR-VL。','',
    '## 结论','',
    '建议暂缓第4项PaddleOCR-VL，先修正旧v4的文字行方向处理，并把PP-OCRv6 small作为下一轮文档服务候选。三个新模型未出现同时在质量、耗时和显存上全面领先的单一赢家。线上配置保持不变。',
    '英文文档评估CER：原v4为14.74%，仅关闭文字行方向分类后为7.75%；v6 small为5.52%、medium为5.59%、v5 server为6.19%。因此不能把相对原配置的全部改善归因于换模型。',
    '街景评估CER：原v4为18.47%，small为18.84%、medium为17.80%、v5 server为15.54%。v5 server点估计最好，但相对原基线的配对95%区间跨零，且采样进程显存约9.3GiB，需更多业务样本确认收益。',
    '详细选择理由与是否测试4见DECISION.md。','',
    '## 测试范围与边界','',
    '- 三个候选分别使用同系列检测与识别模型，官方 PaddlePaddle ONNX 权重按具体 revision 下载，LFS大文件逐个校验SHA256；model-provenance.json记录来源和校验和。',
    '- 使用 XZ-31-002 的独立 GPU 容器。PaddleOCR3.7.0 / PaddleX3.7.2，ONNX Runtime GPU1.20.2，沿用已验证的CUDA库；每候选检测/识别会话均断言存在CUDAExecutionProvider，未启用TensorRT或显式FP16优化。部分算子可能仍由CPU执行，本轮未做算子级profile。',
    '- 统一主要条件：输入长边超过2000时，复用旧框架的初始缩放；检测短边下限736/min，检测像素阈值0.3、框阈值0.5、unclip1.6，文字过滤0.5，识别batch6，并发1。关闭文档方向分类、文档矫正、文字行方向分类。',
    '- 三个候选共600次计时图像处理；补跑关闭方向分类的旧v4对照200次，并因发现重大方向误判现象，追加同环境开启分类的v4对照200次，合计1000次新运行，额外每组预热一次。另引用上一轮旧v4原配置200张作为部署基线参考。',
    '- 固定calibration40（每来源20）和evaluation160（每来源80）。全部参数在本轮正式运行前冻结，没有依据evaluation调参。这套evaluation已在之前轮次查看过，属于工程回归，不能当成新盲测。',
    '- 新模型使用PaddleX前后处理，旧模型使用RapidOCR1.4.4。尽管统一了关键参数，前后处理实现、字符字典及部分默认细节仍不同；这是整条实现的工程比较，不是只更换权重的严格消融，也不代表各模型最优配置。','',
    '## 160张评估集结果','',
    '| 模型/配置 | 来源 | 成功/总数 | 归一化分组CER | 平均耗时ms | P50/P95 ms | 平均耗时/原基线 |',
    '|---|---|---:|---:|---:|---:|---:|']
    for variant,label in LABELS.items():
        for source,s in ss[variant+'/evaluation'].items():
            old=ss['A_default/evaluation'][source]
            text.append(f"| {label} | {source} | {s['success']}/{s['total']} | {s['normalized_cer']:.2%} | {s['mean_ms']} | {s['inference_p50_ms']}/{s['inference_p95_ms']} | {s['mean_ms']/old['mean_ms']:.2f}× |")
    text+=['','计时从BGR数组解码完成后开始，到原图坐标与文字结果组装完成；包含预处理和前后处理，不包含解码、网络、网关、模型加载。因此不能与上一轮客户端P95直接比较。新旧框架的处理范围并非完全相同，耗时表示各实现的实用处理成本。','',
    '## 40张校准集结果','', '| 模型/配置 | 英文文档CER | 街景CER |','|---|---:|---:|']
    for variant,label in LABELS.items():text.append(f"| {label} | {ss[variant+'/calibration']['doclaynet']['normalized_cer']:.2%} | {ss[variant+'/calibration']['rctw17']['normalized_cer']:.2%} |")
    text+=['','## 成对质量差异（评估集，相对原v4基线）','',
    '差值为百分点，负数更好。区间按80张图配对重抽样2000次获得95% bootstrap区间，只反映这批样本的不确定性。','',
    '| 候选 | 来源 | CER差值百分点 | 95%区间 | 改善/持平/退化图片数 |','|---|---|---:|---:|---:|']
    comparisons={}
    for variant in ['M1_v6_small','M2_v6_medium','M3_v5_server']:
        for source in ['doclaynet','rctw17']:
            a={r['id']:r for r in rows if r['variant']=='A_default' and r['split']=='evaluation' and r['source']==source and r['ok']};b={r['id']:r for r in rows if r['variant']==variant and r['split']=='evaluation' and r['source']==source and r['ok']};assert len(a)==len(b)==80
            count=collections.Counter('better' if b[i]['metrics']['normalized_edits']<a[i]['metrics']['normalized_edits'] else 'worse' if b[i]['metrics']['normalized_edits']>a[i]['metrics']['normalized_edits'] else 'same' for i in a)
            delta=100*(ss[variant+'/evaluation'][source]['normalized_cer']-ss['A_default/evaluation'][source]['normalized_cer']);ci=paired_ci(a,b)
            comparisons[variant+'/'+source]={'delta_pp':delta,'bootstrap95_pp':ci,'paired_counts':dict(count)}
            text.append(f"| {LABELS[variant]} | {source} | {delta:+.2f} | [{ci[0]:+.2f}, {ci[1]:+.2f}] | {count['better']}/{count['same']}/{count['worse']} |")
    (BASE/'paired-comparisons.json').write_text(json.dumps(comparisons,indent=2)+'\n')
    text+=['','## 同环境方向处理复核','',
    '追加的v4分类开启对照使用同一新运行环境，仅将旧v4控制组的use_cls开关改回True。与上一轮历史原配置相比，200张中199张的文字和框结果完全一致，另1张有差异；原始差异保留在审计文件中。',
    '方向开关本身在200张中改变81张的输出。单张校准页a5c884…的进一步诊断显示：检测到46行，其中11行被旧分类器高置信度判为180°并旋转，这11行旋转后识别分数全部跌破0.5而被丢弃。这解释了该页整行漏字的重要来源；不应泛化成所有模型或所有输入的方向分类都无用。',
    '诊断见direction-diagnosis.json；同环境对照见raw/A_v4_cls_recheck；历史一致性见direction-recheck-agreement.json。关闭分类只适合输入朝向已确认的业务；倒置图片仍需方向处理策略。','',
    '## 成对比较：相对关闭分类的v4对照','',
    '| 候选 | 来源 | CER差值百分点 | 95%区间 |','|---|---|---:|---:|']
    for variant in ['M1_v6_small','M2_v6_medium','M3_v5_server']:
        for source in ['doclaynet','rctw17']:
            a={r['id']:r for r in rows if r['variant']=='A_v4_no_cls' and r['split']=='evaluation' and r['source']==source and r['ok']};b={r['id']:r for r in rows if r['variant']==variant and r['split']=='evaluation' and r['source']==source and r['ok']}
            delta=100*(ss[variant+'/evaluation'][source]['normalized_cer']-ss['A_v4_no_cls/evaluation'][source]['normalized_cer']);ci=paired_ci(a,b)
            comparisons['vs_v4_no_cls/'+variant+'/'+source]={'delta_pp':delta,'bootstrap95_pp':ci}
            text.append(f"| {LABELS[variant]} | {source} | {delta:+.2f} | [{ci[0]:+.2f}, {ci[1]:+.2f}] |")
    (BASE/'paired-comparisons.json').write_text(json.dumps(comparisons,indent=2)+'\n')
    text+=['','## 显存与模型文件','',
    '每秒采样nvidia-smi计算进程显存，统计本轮新出现进程的最大分配量，排除预先存在的线上服务PID。这不是瞬时CUDA算子峰值，也不能直接推导最大并发数；采样可能漏掉短时峰值。','',
    '| 模型 | 采样进程显存峰值MiB | 检测+识别ONNX文件MiB |','|---|---:|---:|']
    provenance=json.loads((ROOT/'model-benchmark/model-provenance.json').read_text());pairs={'M1_v6_small':['PP-OCRv6_small_det_onnx','PP-OCRv6_small_rec_onnx'],'M2_v6_medium':['PP-OCRv6_medium_det_onnx','PP-OCRv6_medium_rec_onnx'],'M3_v5_server':['PP-OCRv5_server_det_onnx','PP-OCRv5_server_rec_onnx']}
    for variant in ['A_v4_no_cls','M1_v6_small','M2_v6_medium','M3_v5_server']:
        mem=json.loads((BASE/'raw'/variant/'memory.json').read_text());weight=sum(x['bytes'] for x in provenance if x['model'] in pairs.get(variant,[]) and x['file']=='inference.onnx')/(1024**2)
        text.append(f"| {LABELS[variant]} | {mem['new_process_peak_mib']} | {weight:.1f} |" if variant in pairs else f"| {LABELS[variant]} | {mem['new_process_peak_mib']} | — |")
    text+=['','旧v4关闭方向分类时，RapidOCR仍会加载其分类会话，虽然没有调用，因此其显存不是纯检测+识别模型的最小占用。显存数据仅代表本轮单进程顺序处理；RTX4080 16GB上的并发容量需另测。','',
    '## 指标解释及典型案例','',
    '- 沿用v1固定空间分组CER：交集面积/两框较小面积≥0.5连通分组，近似水平顺序拼接，NFKC+casefold+删除空白、保留标点；逐组编辑数求和后除参考字符数。无匹配参考算删除，无匹配预测算插入，ignore及零面积框依原规则处理。',
    '- DocLayNet参考来自PDF文本，公式/不可见文字/阅读顺序有偏差；RCTW来自公开训练部分，存在未标注或忽略区域。CER不是纯识别准确率，不可用1−CER对外承诺准确率。',
    '- comparison.html按上一轮基线的绝对编辑数选出每来源6张校准图，展示全部模型的预测框与错误组。逐图结果见per-image.csv及scored.jsonl；raw保留完整预测与参数。',
    '- 本轮没有测持续吞吐、GPUStack入口并发、双节点扩展、功耗或商业定价。单图耗时和显存只是后续容量评估输入，不能直接计算利润。','',
    '## 是否测试4（PaddleOCR-VL）','',
    '本轮仅完成专用OCR候选测试，没有下载或运行PaddleOCR-VL。执行建议见DECISION.md；先按本轮实际质量与计算成本筛选普通OCR候选，再决定是否需要文档视觉语言模型。','',
    '## 复现与验证','',
    'RUN.md给出运行步骤，frozen-plan.json保存固定参数。模型revision/SHA256见model-provenance.json；依赖见model-benchmark/requirements-runtime-lock.txt。validation.json校验1000次新运行的完整性、图片SHA、CUDA provider及原图坐标边界。源码及评分规则校验和见reproducibility.json。线上部署未修改。']
    (BASE/'REPORT.md').write_text('\n'.join(text)+'\n')
    paths=[ROOT/'model-benchmark'/n for n in ['download_models.py','run_models.py','check_direction.py','diagnose_direction.py','analyze_models.py','make_report.py','validate_results.py','requirements-runtime-lock.txt']]+[ROOT/'evaluate_ocr.py',ROOT/'datasets/ocr-mixed-v1/splits.json',ROOT/'datasets/ocr-mixed-v1/manifest.jsonl']
    (BASE/'reproducibility.json').write_text(json.dumps({str(p.relative_to(ROOT)):hashlib.sha256(p.read_bytes()).hexdigest() for p in paths},indent=2)+'\n');print(BASE/'REPORT.md')
if __name__=='__main__':main()
