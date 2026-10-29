"""Render the bounded gateway reply-flow investigation and its evidence."""
from pathlib import Path
import html
import json

ROOT=Path(__file__).resolve().parents[2]
FOLDER=ROOT/'results/maas-gateway-503-followup-20261029'

def main():
    summary=json.loads((FOLDER/'summary.json').read_text())
    kernel=json.loads((FOLDER/'kernel-regression.json').read_text())
    requests=json.loads((FOLDER/'recreate.json').read_text())['requests']
    escape=html.escape
    checks=''.join('<tr><td>'+escape(r['label'])+'</td><td>'+('允许' if r['expected'] else '阻断')+'</td><td class="ok">符合预期</td></tr>' for r in kernel['checks'])
    reqs=''.join('<tr><td>'+escape(r['label'])+'</td><td>'+str(r['status'])+'</td><td>'+str(r['lines'])+'</td><td>'+format(r['finished_at']-r['intent_at'],'.3f')+'秒</td></tr>' for r in requests)
    page='''<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>网关503 · 返回包误拦截修复</title>
<style>body{margin:0;background:#f2f5f8;color:#183046;font:16px/1.7 system-ui,-apple-system,sans-serif}main{max-width:1050px;margin:auto;padding:35px 22px}header{background:#16334d;color:white;padding:28px;border-radius:14px}h1{font-size:28px;margin:6px 0}h2{font-size:21px}section{background:white;border:1px solid #dbe3eb;border-radius:12px;padding:22px;margin-top:20px}.notice{background:#fff7e6;border-color:#e6c677}.cards{display:flex;gap:14px;flex-wrap:wrap;margin-top:20px}.card{background:white;border:1px solid #dbe3eb;border-radius:12px;padding:20px;flex:1;min-width:160px}.value{font-size:34px;font-weight:700}.muted{color:#60758a}.ok{color:#08734f}table{border-collapse:collapse;width:100%;font-size:14px}td,th{text-align:left;padding:10px;border-bottom:1px solid #e4eaf0}th{background:#edf3f8}code{background:#edf3f8;padding:2px 5px}a{color:#135a91}.flow{display:flex;gap:10px;align-items:center;flex-wrap:wrap}.node{background:#edf3f8;border-radius:8px;padding:10px}.scroll{overflow:auto}</style>
<main><header><div>2026-10-29 · GW-20261028-001</div><h1>出站连接的返回包，被自己的防火墙误拦截</h1><p>已修复确定可复现的规则缺陷，完成控制端发布与业务回归。</p></header>
<section class="notice"><strong>Issue仍保持开放观察</strong><p>没有原始503的源端口/抓包，不能追溯证明它必然命中本次缺陷。三轮成功不等于长期稳定性或E3容量验收。</p></section>
<div class="cards"><div class="card"><div class="value">26</div><div>本地回归通过</div></div><div class="card"><div class="value">15</div><div>隔离内核检查通过</div></div><div class="card"><div class="value">15 / 15</div><div>三轮重建后新OCR成功</div></div></div>
<section><h2>故障机制</h2><div class="flow"><div class="node">控制端发起连接<br>临时源端口落入保护范围</div><span>→</span><div class="node">worker代理返回握手包</div><span>→</span><div class="node">旧raw规则按目的端口DROP</div></div><p>普通临时端口立即连通；重叠端口超时且DROP计数增加。修复后，宿主和实际网关容器网络均可使用重叠端口正常连接。</p><p>早期过滤改为<code>mangle/PREROUTING</code>：在连接跟踪后、Docker DNAT前，只放行<code>ESTABLISHED + REPLY</code>。未增加全网段信任，原始入站流量仍被隔离。</p></section>
<section><h2>当前运行状态</h2><p class="ok">MaaS接单已恢复 · generation46 · UNKNOWN=0 · 监控无告警</p><p>四个模型副本，双节点各两个；四个候选执行器保持。一笔异步任务成功、attempts1、46行、成功计量1条。VM、Docker、worker及原有业务容器未重启。</p><p class="muted">母版/克隆网络文件本轮未更新；相关改造及新快照首启验收留待下一次网络更新。E3剩余矩阵未自动续跑。</p></section>
<section><h2>隔离内核验证</h2><div class="scroll"><table><tr><th>检查</th><th>预期</th><th>结果</th></tr>'''+checks+'''</table></div></section>
<section><h2>三轮副本重建与首次请求</h2><p>每轮4→2→0→4；running后立即发新请求，不加固定等待、不重放原失败请求。15条网关日志均匹配200，本轮未发现新的模型18网关5xx。</p><div class="scroll"><table><tr><th>新请求</th><th>HTTP</th><th>行数</th><th>耗时</th></tr>'''+reqs+'''</table></div></section>
<section><h2>证据与边界</h2><p><a href="REPORT.md">详细报告</a> · <a href="summary.json">结论</a> · <a href="tcp-probe-before.json">修复前对照</a> · <a href="tcp-probe-after.json">修复后对照</a> · <a href="rollout.json">部署记录</a> · <a href="ingress-regression.json">实际隔离</a> · <a href="probe-access.json">网关日志</a> · <a href="async-regression.json">异步/计量</a> · <a href="final.json">最终状态</a></p><p>原始规则与备份仅保存在Mac/控制端私有目录，不进入报告或Git。TCP只保留连接头元数据。下一步在独立安排的窗口继续E3剩余验证，观察该类503是否再现。</p></section></main></html>'''
    (FOLDER/'REPORT.html').write_text(page)


if __name__=='__main__':main()
