import json
from datetime import datetime,timezone,timedelta
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];BASE=ROOT/'results/ocr-service-v4';s=json.loads((BASE/'direction-summary.json').read_text());old=json.loads((ROOT/'results/ocr-model-v3/summary.json').read_text())
lines=['# OCR 方向处理验证','',f'日期：{datetime.now(timezone(timedelta(hours=8))).isoformat()}','',
'结论：保护策略能避免旧方向分类器误旋转正常文字，并显著改善倒置文档；90°/270°仍存在明显退化。对于已知方向，显式纠正更可靠，不能把它当成已实现自动页面方向识别。','',
'## 方法','',
'- 从固定calibration/evaluation按来源抽取32张（每来源16），对原始像素数组无损旋转0/90/180/270度；预测框评分前映回原图坐标，保持原有空间分组CER口径。32张含12张校准和20张评估，不是新的独立测试集。',
'- 4种模式×32张×4角度=512次；对upright/v6另做32×3×2=192次已知角度纠正；guarded原方向补齐全部200张另168次，共872次。',
'- guarded保留原始行先识别，对分类器判180°或识别分数<0.5的行，再识别180°版本；倒置分数高出0.02才采用。比较的只是模型置信度，没有读取答案来选择方向；置信度不等于正确率。','',
'## 同一32张子集的角度对比','',
'下列为归一化空间分组CER（低更好）。子集难度与完整160张不同，不能跨表直接比较百分比。','',
'| 模式 | 来源 | 0° | 90° | 180° | 270° |','|---|---|---:|---:|---:|---:|']
for mode,angles in s['rotation_subset'].items():
 for source in ['doclaynet','rctw17']:lines.append('| '+mode+' | '+source+' | '+' | '.join(f"{angles[str(a)][source]['normalized_cer']:.2%}" for a in [0,90,180,270])+' |')
lines+=['','## 全量原方向评估集（160张）','', '| 模式 | 英文文档CER | 街景CER |','|---|---:|---:|']
for label,key in [('旧v4自动旋转','A_default'),('v4不做行分类','A_v4_no_cls'),('v6 small','M1_v6_small')]:
 d=old[key+'/evaluation'];lines.append(f"| {label} | {d['doclaynet']['normalized_cer']:.2%} | {d['rctw17']['normalized_cer']:.2%} |")
d=s['full_guarded_upright']['evaluation'];lines.append(f"| v4方向保护（本轮） | {d['doclaynet']['normalized_cer']:.2%} | {d['rctw17']['normalized_cer']:.2%} |")
lines+=['','guarded的主要价值是兼顾正常与倒置输入，不是明显提高已经摆正文档的识别质量；相对直接upright会增加识别次数和处理时间。','',
'## 显式旋转与坐标验证','',
f"192次显式纠正后的文字与各自原方向基线比较，不一致数量：{len(s['explicit_pixel_restore_text_mismatches'])}。像素旋转和坐标互逆另有单元测试，实际HTTP的PNG旋转纠正也单独验证。rotation_cw是调用方给出的顺时针纠正角度，不是服务自动预测。",
'','## 使用建议','',
'- 输入已摆正：v4_upright作为速度候选，v6_small作为文档质量候选。',
'- 存在180°倒置风险：v4_guarded作为兼顾方向的候选；继续用实际业务图片验收置信度选择。',
'- 90°/270°或已知拍摄方向：传入明确rotation_cw；如果要自动判断页面四方向，需另测页面级分类器，本轮没有实现。',
'- 当前服务按原始像素方向处理，未新增EXIF自动转正。',
'- CER受PDF参考、公式和标注覆盖影响，不能直接换算成商业准确率。原服务配置保持不变。']
(BASE/'DIRECTION.md').write_text('\n'.join(lines)+'\n');print(BASE/'DIRECTION.md')
