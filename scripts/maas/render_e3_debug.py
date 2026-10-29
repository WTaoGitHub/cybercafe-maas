"""Render saved gateway diagnostics, no server access."""
import html,json
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2];OUT=ROOT/'results/maas-e3-debug-20261029'


def main():
    s=json.loads((OUT/'summary.json').read_text());f=s['failure']
    rows=''.join('<tr><th>'+html.escape(k)+'</th><td>'+html.escape(str(f[k]))+'</td></tr>' for k in ['start_time','response_code','upstream_cluster','upstream_host','response_flags','response_code_details','duration'])
    text='''<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>E3 503调试分析</title><style>body{font:16px/1.7 system-ui,sans-serif;background:#f3f6fa;color:#172435;margin:0}main{max-width:1040px;margin:auto;padding:30px 24px}section,.status{background:white;padding:24px;border-radius:12px;margin:20px 0}.status{background:#fff5df;border-left:6px solid #d39120}h1{font-size:30px;margin-bottom:8px}h2{font-size:22px}table{border-collapse:collapse;width:100%;font-size:14px}th,td{text-align:left;padding:9px;border-bottom:1px solid #dde4ed}td{overflow-wrap:anywhere}a{color:#155b99}.muted{color:#526174}</style><main><h1>E3原生网关503 · 调试分析</h1><p class="muted">2026-10-29 · 基于故障日志、分层调用和一次受控重建</p>
<div class="status"><strong>MaaS已恢复接单，原故障底层触发原因仍未确认。</strong><br>原故障已定位到网关连接worker8代理失败。本次未复现；未更改GPUStack代码、重试策略或防火墙，不将恢复可用等同于根因修复。E3没有自动续跑。</div>
<section><h2>原故障发生在哪里</h2><p>GPUStack网关 → <strong>worker8代理 10.11.31.3:10150</strong> → OCR实例。日志选中的是新实例150，不是找不到模型路由；UF表示上游连接失败。</p><table>'''+rows+'''</table><p><a href="https://www.envoyproxy.io/docs/envoy/v1.35.5/configuration/observability/access_log/usage">Envoy官方标记定义</a></p></section>
<section><h2>本轮实际验证</h2><ul><li>16项逐层检查通过：4个模型直连、4个代理健康检查、4个代理OCR调用、4个网关OCR调用。</li><li>一次4→2→0→4受控重建，首次请求及后续新请求共5次成功；未重放原失败业务请求。</li><li>TCP只记录连接头元数据，30个SYN均观察到SYN-ACK，没有图片、凭据或OCR文本载荷。</li><li>正式MaaS恢复探针46行通过，paused=false、generation44、UNKNOWN=0、监控无告警。</li></ul><p>当前4副本、双卡各2份，原配置哈希匹配；4个候选执行器及额度4保持。没有重启VM、Docker或worker。</p></section>
<section><h2>仍不确定的部分</h2><p>原失败时没有TCP抓包，缺少全部历史模型启动日志；不能确认是瞬时网络、代理连接可接纳性还是配置/连接池切换时序。日志显示当时正在推送路由，但时间接近不构成因果证明。</p><p>后续应在受控复测时保留连接元数据和Envoy证据，失败即停。不通过固定sleep或自动推理重试把故障隐藏，也不恢复collector。</p><p><a href="REPORT.md">详细报告</a> · <a href="summary.json">汇总数据</a> · <a href="access-window.json">故障访问记录</a> · <a href="recovery.json">恢复记录</a> · <a href="final.json">最终状态</a></p></section></main></html>'''
    (OUT/'REPORT.html').write_text(text)


if __name__=='__main__':main()
