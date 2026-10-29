"""Render sanitized stage evidence; missing, interrupted or short runs never pass."""
import argparse
import html
import json
from pathlib import Path


FILES={'T0-BASELINE':'t0-baseline.json','T1-NATIVE':'t1-native.json','T2-ASYNC':'t2-async.json',
    'T3-LIMITS':'t3-limits.json','T3-EXTEND':'t3-extend.json','T4-QUEUE':'t4-queue.json',
    'T4-HTTP-ACCEPT':'t4-http-accept.json','T4-HTTP-REJECT':'t4-http-reject.json','T4-HTTP-RECOVER':'t4-http-recover.json',
    'T5-CLAIM':'t5-claim.json','T6-CONFIRM':'t6-confirm.json','T6-SOAK':'t6-soak.json'}


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
    return {'state':'COMPLETE' if all(gates.values()) else 'INCOMPLETE','gates':gates,'automatic_production_promotion':False,
        'selected_parameters':confirmed if all(gates.values()) else None,'maximum_capacity_proven':False,'production_sla_proven':False}


def render(directory):
    directory=Path(directory);evidence={stage:json.loads((directory/name).read_text()) for stage,name in FILES.items() if (directory/name).exists()}
    summary=assess(evidence);(directory/'SUMMARY.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2)+'\n')
    rows=[];sections=[]
    for stage,name in FILES.items():
        value=evidence.get(stage,{});state=value.get('state','PENDING');points=value.get('points',value.get('rounds',value.get('runs',[])))
        rows.append('<tr><td>'+html.escape(stage)+'</td><td>'+html.escape(state)+'</td><td>'+str(len(points))+'</td></tr>')
        if value:
            sections.append('<details><summary>'+html.escape(stage)+' · '+html.escape(state)+'</summary><pre>'+html.escape(json.dumps(value,ensure_ascii=False,indent=2))+'</pre><a href="'+name+'">JSON evidence</a></details>')
    text='''<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>E3 参数调优验收</title><style>
    body{margin:0;background:#f3f5f8;color:#17263b;font:16px/1.65 system-ui,sans-serif}main{max-width:1120px;margin:auto;padding:36px 24px}h1{font-size:32px;margin-bottom:8px}.card,details{background:white;border:1px solid #dce3eb;border-radius:12px;padding:20px;margin:18px 0}table{border-collapse:collapse;width:100%}th,td{padding:10px;border-bottom:1px solid #e4e9f0;text-align:left}summary{font-weight:650;cursor:pointer}pre{white-space:pre-wrap;overflow-wrap:anywhere;font-size:13px}a{color:#245db8}.status{font-size:22px;font-weight:700}</style><main>
    <h1>PP-OCRv4 · E3 参数调优</h1><div class="card"><div class="status">'''+summary['state']+'''</div><p>两张 RTX 4080 SUPER，GPUStack v2.2.3，固定模型流水线。普通集与边界混合集分别计量；受理、拒绝、OCR完成分开统计。</p><p>未完成阶段不算通过；2小时运行不替代8小时或生产SLA。未自动发布推荐配置，不重启VM、worker或Docker引擎。</p></div>
    <div class="card"><table><thead><tr><th>阶段</th><th>证据状态</th><th>点/轮数</th></tr></thead><tbody>'''+''.join(rows)+'''</tbody></table></div><div class="card"><h2>最终门槛</h2><pre>'''+html.escape(json.dumps(summary,ensure_ascii=False,indent=2))+'''</pre></div>'''+''.join(sections)+'''<p>采样只能支持其实际分辨率内的观察，不能证明任意时刻不断粮。数字只适用于本profile和已测输入；其他模型、硬件或GPUStack版本需要重新验证。</p></main></html>'''
    (directory/'REPORT.html').write_text(text)
    return summary


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--directory',required=True);a=p.parse_args();print(json.dumps(render(a.directory)))
