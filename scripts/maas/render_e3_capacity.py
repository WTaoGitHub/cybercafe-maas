"""Render saved E3 evidence only; no network or service mutation."""
import html,json
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2];OUT=ROOT/'results/maas-e3-20261028'


def main():
    s=json.loads((OUT/'summary.json').read_text());rows=[]
    for r in s['runs']:
        rows.append('<tr>'+''.join('<td>'+html.escape(str(v))+'</td>' for v in [r['run'],r['executors'],f"{r['throughput']:.3f}",f"{r['p95']:.3f}",r['database']['success'],r['database']['events']])+'</tr>')
    points=[]
    for p in s['matrix_points']:
        values=[p['name'],p['phase'],p['replicas'],f"{p['throughput_images_per_second']:.3f}" if p['throughput_images_per_second'] else '未取得',f"{p['p95_seconds']:.3f}" if p['p95_seconds'] else '未取得']
        points.append('<tr>'+''.join('<td>'+html.escape(str(v))+'</td>' for v in values)+'</tr>')
    native=''.join(points);throughput=''.join(rows)
    document='''<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>E3阶段结果 · 已中断</title>
<style>body{font:16px/1.7 system-ui,sans-serif;background:#f2f5f8;color:#182334;margin:0}main{max-width:1080px;margin:auto;padding:32px 24px}h1{font-size:30px;margin-bottom:8px}h2{margin-top:32px}section,.alert{background:white;padding:22px;border-radius:12px;margin:20px 0}.alert{border-left:6px solid #c63832;background:#fff1ef}table{width:100%;border-collapse:collapse;font-size:14px}td,th{text-align:left;padding:10px;border-bottom:1px solid #dce3eb}th{background:#edf2f8}a{color:#155ba0}.scroll{overflow-x:auto}.note{color:#526174}</style><main>
<h1>E3并发与容量 · 阶段报告</h1><p class="note">2026-10-28 · 数据来自真实试点与隔离回归，不代表完整E3验收或生产SLA</p>
<div class="alert"><strong>已主动中断：双卡4副本预热，GPUStack原生网关HTTP503。</strong><br>失败请求未重试。MaaS保持维护暂停（generation43），4个候选执行器/全局及项目额度4；模型仍4副本、双卡各2 running。未执行恢复接单探针。</div>
<section><h2>已验证的异步链路</h2><p>固定原4模型副本及2GiB声明，40张相同原图，每轮另加2个预热任务。客户端并发与执行器数一致。210个任务全部成功，attempts=1，逐task成功计量各1条。</p><div class="scroll"><table><tr><th>轮次</th><th>执行器</th><th>张/秒</th><th>P95秒</th><th>成功任务</th><th>成功事件</th></tr>'''+throughput+'''</table></div><p class="note">1/2执行器各两轮，4执行器仅一轮，尚无最终稳定性结论。端到端包含领取、排队、轮询和结果下载开销。</p></section>
<section><h2>原生副本矩阵</h2><p>MaaS暂停期间，固定4客户端经原生GPUStack网关调用；每点44图，包含3MP/9MP的PNG/JPEG。该链路不经过MaaS任务/计量，不能直接与上表比较。</p><div class="scroll"><table><tr><th>点位</th><th>状态</th><th>副本</th><th>张/秒</th><th>P95秒</th></tr>'''+native+'''</table></div><p>前7点308次测量请求成功；dual-4预热失败，dual-8未执行。镜像已缓存，启动测量为进程冷启动。</p></section>
<section><h2>当前边界与后续</h2><p>286项隔离回归通过，真实停库专项本轮不重复。原2GiB claim未降低；原生指标峰值为低频观察下界，不能直接除以副本数推算安全预算，未提供功耗结论。</p><p>先排查503，再明确恢复和真实探针；剩余矩阵、稳定复核及最终配置选型均未完成。依用户要求，不自动修复、重试、恢复接单或重启VM/Docker。</p><p><a href="REPORT.md">详细报告</a> · <a href="summary.json">汇总数据</a> · <a href="interruption-state.json">中断现场</a> · <a href="tests.xml">隔离JUnit结果</a></p></section></main></html>'''
    if s.get('subsequent_debug',{}).get('admission_restored'):
        document=document.replace('MaaS保持维护暂停（generation43），','10月28日中断时MaaS维护暂停（generation43），')
        document=document.replace('未执行恢复接单探针。</div>', '该状态为10月28日现场；10月29日分层调试及一次重建未复现503，正式探针通过后已恢复接单（generation44）。底层触发未证实，E3未续跑。<a href="../maas-e3-debug-20261029/REPORT.md">最新调试报告</a>。</div>')
        document=document.replace('依用户要求，不自动修复、重试、恢复接单或重启VM/Docker。','10月28日依用户要求中断；10月29日按调试恢复条件完成探针并恢复接单，未自动续跑E3或重启VM/Docker。')
    (OUT/'REPORT.html').write_text(document)


if __name__=='__main__':main()
