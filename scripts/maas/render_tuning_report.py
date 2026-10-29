"""Render sanitized stage evidence; missing, interrupted or short runs never pass."""
import argparse
import html
import json
from pathlib import Path


FILES={'T0-BASELINE':'t0-baseline.json','T1-NATIVE':'t1-native.json','T2-ASYNC':'t2-async.json',
    'T3-LIMITS':'t3-limits.json','T3-EXTEND':'t3-extend.json','T4-QUEUE':'t4-queue.json',
    'T4-HTTP-ACCEPT':'t4-http-accept.json','T4-HTTP-REJECT':'t4-http-reject.json','T4-HTTP-RECOVER':'t4-http-recover.json',
    'T5-CLAIM':'t5-claim.json','T6-CONFIRM':'t6-confirm.json','T6-SOAK':'t6-soak.json'}
SUPPLEMENTAL={'T4-QUEUE-VERIFICATION':'t4-verification.json','RUNTIME-RESTORATION':'runtime-restoration.json'}


def assess(evidence):
    gates={}
    for stage in FILES:
        v=evidence.get(stage,{})
        gates[stage]=v.get('state')=='COMPLETE' or stage=='T3-EXTEND' and v.get('state')=='SKIPPED_CONDITION_NOT_MET'
    soak=evidence.get('T6-SOAK',{});segments=[p for p in soak.get('points',[]) if p.get('point_id','').startswith('T6-SOAK-segment-')]
    confirmed=evidence.get('T6-CONFIRM',{}).get('selected_candidate',{}).get('parameters')
    gates['T6-SOAK-COVERAGE']=len(segments)==24 and all(p.get('state')=='COMPLETE' and p.get('measurement_seconds',0)>=300 for p in segments) and sum(p.get('measurement_seconds',0) for p in segments)>=7200 and soak.get('wall_seconds',0)>=7200 and len(soak.get('controlled_executor_drain',[]))==1
    gates['JOINT-TUPLE']=bool(confirmed and confirmed==soak.get('selected_parameters'))
    baseline=evidence.get('T0-BASELINE',{}).get('parameters')
    gates['RESTORED']=bool(baseline and baseline==soak.get('restored_parameters'))
    restoration=evidence.get('RUNTIME-RESTORATION',{})
    restored=bool(baseline and restoration.get('state')=='COMPLETE' and
        restoration.get('restored_parameters')==baseline and restoration.get('paused') is False and
        restoration.get('unknown')==0 and restoration.get('real_resume_probes_passed',0)>=2)
    return {'state':'COMPLETE' if all(gates.values()) else 'INCOMPLETE','gates':gates,'automatic_production_promotion':False,
        'selected_parameters':confirmed if all(gates.values()) else None,'maximum_capacity_proven':False,'production_sla_proven':False,
        'interrupted_stages':[k for k in FILES if evidence.get(k,{}).get('state','').startswith('INTERRUPTED')],
        'not_run_stages':[k for k in FILES if evidence.get(k,{}).get('state','PENDING').startswith(('NOT_RUN','PENDING'))],
        'runtime_restored_after_interruption':restored,
        'restoration_scope':'runtime recovery evidence is separate from final T6 soak restoration gate'}


def render(directory):
    directory=Path(directory);evidence={stage:json.loads((directory/name).read_text()) for stage,name in {**FILES,**SUPPLEMENTAL}.items() if (directory/name).exists()}
    summary=assess(evidence);(directory/'SUMMARY.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2)+'\n')
    rows=[];sections=[]
    for stage,name in FILES.items():
        value=evidence.get(stage,{});state=value.get('state','PENDING');points=value.get('points',value.get('rounds',value.get('runs',[])))
        rows.append('<tr><td>'+html.escape(stage)+'</td><td>'+html.escape(state)+'</td><td>'+str(len(points))+'</td></tr>')
        if value:
            sections.append('<details><summary>'+html.escape(stage)+' · '+html.escape(state)+'</summary><pre>'+html.escape(json.dumps(value,ensure_ascii=False,indent=2))+'</pre><a href="'+name+'">JSON evidence</a></details>')
    for stage,name in SUPPLEMENTAL.items():
        if stage in evidence:
            sections.append('<details><summary>'+html.escape(stage)+'</summary><pre>'+html.escape(json.dumps(evidence[stage],ensure_ascii=False,indent=2))+'</pre><a href="'+name+'">JSON evidence</a></details>')
    outcome=''
    if summary['interrupted_stages']:
        interrupted=', '.join(summary['interrupted_stages'])
        recovery='已恢复测试前参数，真实探针通过。' if summary['runtime_restored_after_interruption'] else '尚无完整的恢复证明。'
        outcome='<div class="card"><h2>本轮已中断，未形成最终推荐</h2><p>'+html.escape(interrupted)+' 未通过阶段门槛，后续阶段未继续。'+recovery+'</p><p>这里的运行恢复与最终门槛 RESTORED 分开：后者要求完成 T6 两小时运行后的恢复，本轮尚未执行。</p><p><a href="REPORT.md">完整分析与下一步</a> · <a href="ARTIFACT-LIFECYCLE-FINDING.md">文件清理诊断与因果边界</a></p></div>'
    policy=evidence.get('T4-QUEUE-VERIFICATION',{}).get('queue_policy_analysis',[])
    if policy:
        verification=evidence['T4-QUEUE-VERIFICATION']
        policy_rows=''.join('<tr><td>'+str(p['queue'])+'</td><td>'+str(p['normal_rejections']['0.5'])+'</td><td>'+str(p['normal_rejections']['0.8'])+'</td><td>'+format(p['maximum_wait_p95'],'.3f')+'</td><td>'+('通过' if p['eligible'] else '未通过')+'</td></tr>' for p in policy)
        decision='没有队列满足全部选型条件。' if not verification.get('eligible_queues') else '合格候选见下表；尚需后续联合验证。'
        outcome+='<div class="card"><h2>T4 队列选型结果</h2><p>参考 μ 来自前阶段短时粗筛，不能视为当前稳定容量。选型要求正常到达零拒绝、各负载等待 P95 ≤ 5 秒并保留补量储备；'+decision+'</p><table><thead><tr><th>队列上限</th><th>0.5μ 拒绝数</th><th>0.8μ 拒绝数</th><th>各负载最大等待 P95 / 秒</th><th>选型</th></tr></thead><tbody>'+policy_rows+'</tbody></table><p>'+format(verification['accepted'],',')+' 个已受理任务全部成功；'+format(verification['rejected'],',')+' 次队列拒绝独立统计，不算执行失败或成功吞吐。冻结的 5 秒预算未调整。</p></div>'
    text='''<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>E3 参数调优验收</title><style>
    body{margin:0;background:#f3f5f8;color:#17263b;font:16px/1.65 system-ui,sans-serif}main{max-width:1120px;margin:auto;padding:36px 24px}h1{font-size:32px;margin-bottom:8px}.card,details{background:white;border:1px solid #dce3eb;border-radius:12px;padding:20px;margin:18px 0}table{border-collapse:collapse;width:100%}th,td{padding:10px;border-bottom:1px solid #e4e9f0;text-align:left}summary{font-weight:650;cursor:pointer}pre{white-space:pre-wrap;overflow-wrap:anywhere;font-size:13px}a{color:#245db8}.status{font-size:22px;font-weight:700}</style><main>
    <h1>PP-OCRv4 · E3 参数调优</h1><div class="card"><div class="status">'''+summary['state']+'''</div><p>两张 RTX 4080 SUPER，GPUStack v2.2.3，固定模型流水线。受理、拒绝、OCR完成分开统计；普通集与边界混合集的证据按实际已执行阶段区分。</p><p>未完成阶段不算通过；2小时运行不替代8小时或生产SLA。未自动发布推荐配置，不重启VM、worker或Docker引擎。</p></div>
    '''+outcome+'''<div class="card"><table><thead><tr><th>阶段</th><th>证据状态</th><th>点/轮数</th></tr></thead><tbody>'''+''.join(rows)+'''</tbody></table></div><div class="card"><h2>最终门槛</h2><pre>'''+html.escape(json.dumps(summary,ensure_ascii=False,indent=2))+'''</pre></div>'''+''.join(sections)+'''<p>采样只能支持其实际分辨率内的观察，不能证明任意时刻不断粮。数字只适用于本profile和已测输入；其他模型、硬件或GPUStack版本需要重新验证。</p></main></html>'''
    (directory/'REPORT.html').write_text(text)
    return summary


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--directory',required=True);a=p.parse_args();print(json.dumps(render(a.directory)))
