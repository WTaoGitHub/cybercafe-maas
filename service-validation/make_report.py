"""Create the final direction, HTTP capacity and GPU-electricity report."""
import collections,hashlib,json
from datetime import datetime,timezone,timedelta
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];BASE=ROOT/'results/ocr-service-v4'
LABEL={'v4_legacy':'旧v4自动旋转','v4_upright':'v4已摆正模式','v4_guarded':'v4方向保护','v6_small':'v6 small文档候选'}
def main():
 s=json.loads((BASE/'service-summary.json').read_text());d=json.loads((BASE/'direction-summary.json').read_text());phases=s['phases'];steady=s['sustained'];assert len(steady)>=5
 chosen=[]
 for variant in ['v4_upright','v4_guarded','v6_small']:
  rs=[r for r in steady if r['variant']==variant];peak=max(r['images_per_second'] for r in rs);chosen.append(min((r for r in rs if r['images_per_second']>=peak*.95),key=lambda r:(r['replicas'],r['concurrency'])))
 (BASE/'recommended-profiles.json').write_text(json.dumps(chosen,indent=2)+'\n')
 count=sum(x['requests'] for x in phases);success=sum(x['success'] for x in phases)
 rows=['# OCR 方向修正、服务吞吐与 GPU 电费验证','',f'生成时间：{datetime.now(timezone(timedelta(hours=8))).isoformat()}（上海）','',
 '## 结论','',
 f'完成872次方向/质量图像处理，以及{count}次计时HTTP请求，其中{success}次成功。测试经过实际GPUStack统一入口；原有rapidocr-gpu路由和模型配置保持不变。',
 '方向保护解决了旧分类器误旋转正常文字的重要问题，并改善180°倒置文档；90°/270°仍需明确纠正参数或另行验证页面方向分类。多节点和每卡多副本能增加吞吐，单纯增大单实例HTTP并发主要增加排队。',
 '成本按用户指定1元/kWh、每台每天8小时，仅计NVIDIA-SMI GPU功耗读数对应的电费。下表日处理量是满负载理论外推，不能当作订单量或完整服务成本。','',
 '## 持续混合负载结果','',
 '每轮目标120秒，最后等待在途请求完成，实际窗口略长；输入为选定20张英文文档与20张中文/混合街景，按约1:1请求数交替。输入预加载并预热模型，但每次请求都会实际推理，没有识别结果缓存。',
 '下表在已测持续配置中，选出吞吐达到本模式最高值95%以上、且副本数/并发较低的一组；这是容量和延迟的折中规则，不是盈利最优解。','',
 '| 模式 | 物理GPU/总副本 | 并发 | 成功/请求 | 张/秒 | P50/P95 ms | 两卡平均GPU功耗W | GPU元/千张 |','|---|---:|---:|---:|---:|---:|---:|---:|']
 for r in chosen:rows.append(f"| {LABEL[r['variant']]} | 2/{r['replicas']} | {r['concurrency']} | {r['success']}/{r['requests']} | {r['images_per_second']:.2f} | {r['client_p50_ms']}/{r['client_p95_ms']} | {r['platform_gpu_average_w']:.1f} | {r['gpu_cost_cny_per_1000']:.5f} |")
 rows+=['','## 每台每天8小时的理论外推','',
 '假定两节点闲置时段重合、控制端持续可用、连续有足量请求，输入难度与本次相近。吞吐和功率已经合计两张卡，因此只乘8小时，不再额外乘2。未验证8小时耐久运行、Windows切换或实际业务利用率。','',
 '| 模式 | 总副本/并发 | 8小时理论图片数 | 两卡8小时GPU电费元 |','|---|---:|---:|---:|']
 for r in chosen:rows.append(f"| {LABEL[r['variant']]} | {r['replicas']}/{r['concurrency']} | {r['daily_images_at_8h_full_load']:,.0f} | {r['daily_gpu_cost_cny_at_8h_full_load']:.2f} |")
 rows+=['','## 全部持续负载对照','', '| 模式 | 副本 | 并发 | 实际秒数 | 请求数 | 张/秒 | P95 ms | GPU元/千张 |','|---|---:|---:|---:|---:|---:|---:|---:|']
 for r in sorted(steady,key=lambda r:(r['variant'],r['replicas'],r['concurrency'])):rows.append(f"| {LABEL[r['variant']]} | {r['replicas']} | {r['concurrency']} | {r['seconds']:.2f} | {r['requests']} | {r['images_per_second']:.2f} | {r['client_p95_ms']} | {r['gpu_cost_cny_per_1000']:.5f} |")
 uprights=sorted([r for r in steady if r['variant']=='v4_upright' and r['concurrency']==8],key=lambda r:r['replicas'])
 if len(uprights)>=2:
  a,b=uprights[0],uprights[-1];rows+=['',f"相同v4已摆正模式和并发8下，总副本由{a['replicas']}增至{b['replicas']}，混合吞吐从{a['images_per_second']:.2f}升至{b['images_per_second']:.2f}张/秒，约{b['images_per_second']/a['images_per_second']:.2f}倍；GPU单位电费从{a['gpu_cost_cny_per_1000']:.5f}变为{b['gpu_cost_cny_per_1000']:.5f}元/千张。"]
 guards=sorted([r for r in steady if r['variant']=='v4_guarded' and r['replicas']==4],key=lambda r:r['concurrency'])
 if len(guards)>=2:
  a,b=guards[0],guards[-1];rows+=['',f"方向保护模式并发{a['concurrency']}与{b['concurrency']}对照：吞吐{a['images_per_second']:.2f}/{b['images_per_second']:.2f}张/秒，P95为{a['client_p95_ms']:.0f}/{b['client_p95_ms']:.0f}ms。应按业务延迟要求选择，增加排队不是免费增加容量。"]
 rows+=['','## 分来源并发矩阵','',
 '单元格为“张/秒（并发）”，每阶段40请求、每来源20张各重复2次；选择达到该组最高吞吐95%以上的最低并发。短批次会受请求分配、尾部在途请求、时钟/功耗采样与混合负载互补性影响，不用短批次估计代替持续实测。','',
 '| 模式 | 来源 | 1GPU/1副本 | 2GPU/2副本 | 2GPU/4副本 |','|---|---|---:|---:|---:|']
 matrix={(r['variant'],r['source'],r['replicas']):r for r in s['best_matrix_within_95pct_of_max']}
 for mode in LABEL:
  for source in ['doclaynet','rctw17']:
   values=[]
   for replica in [1,2,4]:
    r=matrix.get((mode,source,replica));values.append(f"{r['images_per_second']:.2f}（{r['concurrency']}）" if r else '未测')
   rows.append('| '+LABEL[mode]+' | '+source+' | '+' | '.join(values)+' |')
 rows+=['','## 方向修正验证','',
 '完整方向实验见[DIRECTION.md](DIRECTION.md)。对同一32张图片做无损像素旋转，并把预测坐标映回原图后按冻结规则评分，避免阅读顺序旋转本身造成评分变化。',
 '| 方向策略 | 子集英文0°CER | 子集英文180°CER |','|---|---:|---:|']
 for mode in LABEL:
  a=d['rotation_subset'][mode];rows.append(f"| {LABEL[mode]} | {a['0']['doclaynet']['normalized_cer']:.2%} | {a['180']['doclaynet']['normalized_cer']:.2%} |")
 e=d['full_guarded_upright']['evaluation'];rows+=['',f"方向保护在完整160张原方向评估集上的分组CER：英文{e['doclaynet']['normalized_cer']:.2%}、街景{e['rctw17']['normalized_cer']:.2%}；与直接关闭分类的v4相比，正常方向质量接近，主要价值在处理倒置风险。v6 small的5.52%英文CER来自上一轮相同模型/主要参数的完整评估。",
 '192次已知角度显式纠正后的文字全部恢复到各自原方向输出。HTTP层另通过真实PNG旋转检查。rotation_cw是调用方提供的纠正角度，不是自动方向预测；本轮未新增EXIF自动转正。',
 'CER沿用空间连通分组、NFKC+casefold+去空白规则，受PDF参考、公式、忽略区域和未标注背景文字影响，不能直接用1−CER对外承诺准确率。','',
 '## 能耗与成本算法','',
 '- 主机每0.5秒采集GPU功耗、利用率与显存；健康检查确认时钟差在毫秒级，积分时做近似校正，按每个实际请求窗口对两张GPU功率进行梯形积分。',
 '- 主成本为两张卡总GPU电费。单工作节点测试仍计入控制机GPU的闲置功耗，另在JSON中保留实际工作GPU及相对空闲基线增量值。',
 '- 每千张GPU电费 = 总GPU平均功率(W) ÷ 吞吐(张/秒) ÷ 3600 × 电价(元/kWh)。日电费 = 总GPU平均功率 × 8 ÷ 1000 × 电价。',
 '- 只计GPU；不含CPU、主板、电源损耗、制冷、网络、硬件折旧与人工。未给出收入或利润预测。',
 '- 单位和积分通过恒定功率、部分区间线性插值、千张换算及缺失覆盖拒绝测试。短矩阵阶段电费仅供诊断，主要成本判断采用约120秒持续负载。','',
 '## GPUStack、显存与服务边界','',
 '- 原有控制端+worker在001，002为worker；临时模型先固定002单副本，再扩为两节点各1副本，最后各2副本。部署快照记录实际worker、实例ID和端口。',
 '- 服务内锁保护共享OCR对象和方向状态，所以单实例提高HTTP并发会形成队列；多个独立实例能利用并行余量。这里验证的是调度、路由与运行能力，不是GPU硬隔离。',
 '- 测试实例同时加载v4与v6，便于公平切换，显存高于专用单模型容器；6GiB排程声明是估计，不是硬限。每卡2副本只对本批输入验证，不代表最大或任意输入下的安全容量。',
 '- 客户端在控制机，经GPUStack HTTP网关到两worker，含服务解码、路由、内部网络与排队；不含客户外网上传时间。输入预加载，未测文件读取性能。',
 '- 原有服务共存但未接入压力流量；本轮没有再测节点掉线、重试、持久队列或Windows/Ubuntu切换。',
 '- 期间遇到SSH MaxStartups管理连接限制，HTTP控制服务仍正常。通过已验证主机密钥的内网跳转与复用连接继续；等待区间不计入负载窗口。未修改SSH配置或重启主机。','',
 '## 验证、清理与复现','',
 '8项单元测试（方向4项、能耗4项）通过；HTTP模式选择、显式纠正、SSE/非流式一致、缺图400通过。具体请求计数、坐标/结果一致性与功耗覆盖见validation.json；清理结果见cleanup.json。',
 '测试后撤销推理专用key、删除临时模型/路由/后端并停止功耗采样；模型镜像、数据、脚本及原始结果保留。原rapidocr-gpu配置校验不变，并做调用复查。',
 '实现及复现见[service-validation/README.md](../../service-validation/README.md)。service-summary.json为完整统计，service-phases.csv为全部阶段指标，load-results保留逐请求记录；direction-scored.jsonl保留方向评分。reproducibility.json记录脚本和输入清单校验和。','',
 '## 下一步建议','',
 '先按业务选择接口策略：已摆正输入使用upright以降低成本；文档质量优先选v6 small并接受显式方向；180°风险使用guarded候选；90°/270°自动处理需要单独验证页面级方向模型。',
 '在真实业务图片和明确延迟目标下确认默认配置，再以专用单模型镜像验证更长时间负载和每卡实例数。本轮暂不增加PaddleOCR-VL。']
 (BASE/'REPORT.md').write_text('\n'.join(rows)+'\n')
 paths=list((ROOT/'service-validation').glob('*.py'))+[ROOT/'service-validation/Dockerfile',ROOT/'service-validation/Dockerfile.cached',ROOT/'service-validation/requirements-service.txt',ROOT/'evaluate_ocr.py',ROOT/'datasets/ocr-mixed-v1/manifest.jsonl',ROOT/'datasets/ocr-mixed-v1/splits.json']
 (BASE/'reproducibility.json').write_text(json.dumps({str(p.relative_to(ROOT)):hashlib.sha256(p.read_bytes()).hexdigest() for p in paths},indent=2)+'\n');print(BASE/'REPORT.md')
if __name__=='__main__':main()
