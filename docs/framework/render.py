"""Render self-contained design overview and a standalone component SVG."""
from pathlib import Path
import html,re
P=Path(__file__).resolve().parent
parts=['<svg xmlns="http://www.w3.org/2000/svg" width="1200" height="850" viewBox="0 0 1200 850" role="img" aria-labelledby="title desc"><title id="title">统一模型服务组件架构</title><desc id="desc">客户通过网关和框架API提交持久任务，执行器调用GPUStack管理的模型实例，结果保存在控制端。控制端必须常在线。</desc><defs><marker id="arrow" markerWidth="8" markerHeight="8" refX="7" refY="4" orient="auto"><path d="M0 0 L8 4 L0 8" fill="#5b728a"/></marker></defs><style>text{font-family:-apple-system,BlinkMacSystemFont,Segoe UI,PingFang SC,Microsoft YaHei,sans-serif;fill:#213d56} .label{font-size:15px;font-weight:600}.small{font-size:12px;fill:#566f85}</style><rect width="1200" height="850" fill="#f5f8fc"/><text x="38" y="38" font-size="24" font-weight="700">统一模型服务 · 组件架构</text><text x="38" y="64" class="small">目标架构 / 设计提案 · 2026-10-20 · 现有 GPUStack 与新增平台组件分工</text>']
def box(x,y,w,h,title,sub='',color='#fff',stroke='#b9cbd9'):
 parts.append(f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="10" fill="{color}" stroke="{stroke}"/><text x="{x+w/2}" y="{y+27}" text-anchor="middle" class="label">{html.escape(title)}</text>')
 if sub:parts.append(f'<text x="{x+w/2}" y="{y+49}" text-anchor="middle" class="small">{html.escape(sub)}</text>')
def arrow(x1,y1,x2,y2,label='',dash=False):
 parts.append(f'<path d="M{x1} {y1} L{x2} {y2}" fill="none" stroke="#5b728a" stroke-width="1.6" marker-end="url(#arrow)"'+(' stroke-dasharray="6 4"' if dash else '')+'/>')
 if label:parts.append(f'<text x="{(x1+x2)/2+7}" y="{(y1+y2)/2-7}" class="small">{html.escape(label)}</text>')
parts.append('<rect x="30" y="175" width="1140" height="442" rx="16" fill="#eaf1f8" stroke="#bdcfdf"/><text x="50" y="201" class="label">001 常在线控制端（当前亦提供 GPU worker）</text>')
box(55,90,190,64,'客户 / SDK','统一模型别名与任务接口')
box(935,90,215,64,'本地 Mac 管理员','SSH 私有管理访问')
box(55,230,190,70,'客户网关','HTTPS / 请求与连接限制')
box(295,230,300,70,'框架 API + 模型目录','鉴权 / schema / 幂等 / 查询')
box(690,230,240,70,'PostgreSQL','任务队列 / 租约 / 用量 / 状态','#fff7df')
box(295,350,300,70,'执行器 + GPUStack connector','领取任务 / 并发槽 / 适配器调用')
box(690,350,240,70,'ArtifactStore','输入与权威结果 / 保留与清理','#fff7df')
box(55,470,210,70,'协调器','回收 / 重试 / 节点排空')
box(350,470,245,70,'现有 GPUStack 控制端','GPU 放置 / 模型部署 / 副本','#dff1e8')
box(690,470,240,70,'平台观测','结构化日志 / 指标 / 告警')
parts.append('<text x="965" y="275" class="small">新增平台核心</text><text x="965" y="300" class="small">首版不拆微服务</text><text x="965" y="370" class="small">控制端持久卷</text><text x="965" y="395" class="small">worker 仅留缓存</text><text x="965" y="500" class="small">已有部署继续复用</text>')
arrow(150,154,150,230);arrow(245,265,295,265);arrow(595,265,690,265)
arrow(445,300,445,350);arrow(595,375,690,285,'任务租约')
arrow(595,395,690,395,'持久提交结果')
arrow(360,420,220,470,'恢复与排空',True)
arrow(470,420,470,470,'实例发现',True)
arrow(595,505,690,505,'指标',True)
arrow(1040,154,550,230,'私有管理',True)
parts.append('<rect x="30" y="650" width="1140" height="145" rx="16" fill="#eef6f1" stroke="#b9d3c4"/><text x="50" y="677" class="label">GPU worker 资源池 · 模型适配器协议统一，输入输出按能力定义</text>')
box(55,700,300,70,'001 worker / GPU 实例','现有 OCR 适配器 · 临时缓存','#fff')
box(435,700,300,70,'002 worker / GPU 实例','可排空退出 · 临时缓存','#fff')
box(815,700,300,70,'后续 worker / 模型适配器','更多节点与模型','#fff')
parts.append('<path d="M300 385 L280 385 L280 580 L205 580 L205 700" fill="none" stroke="#196d96" stroke-width="2" marker-end="url(#arrow)"/><path d="M560 420 L630 420 L630 630 L585 630 L585 700" fill="none" stroke="#196d96" stroke-width="2" marker-end="url(#arrow)"/><text x="300" y="602" class="small">定向推理：仅选择 READY 实例；结果返回执行器</text>')
arrow(460,540,350,700,'部署',True);arrow(495,540,780,700,'部署与生命周期',True)
parts.append('<text x="38" y="828" class="small">实线：请求 / 任务 / 数据路径　虚线：管理与控制路径　严格排空须纳入框架，旧 Playground 旁路请求不自动受控。</text></svg>')
svg=''.join(parts);(P/'ARCHITECTURE.svg').write_text(svg)
def inline(s):
 s=html.escape(s)
 s=re.sub(r'`([^`]+)`',r'<code>\1</code>',s)
 return re.sub(r'\[([^\]]+)\]\(([^)]+)\)',lambda m:'<a href="'+m[2]+'">'+m[1]+'</a>',s)
def render(text):
 lines=text.splitlines();out=[];i=0
 while i<len(lines):
  s=lines[i].strip()
  if s.startswith('```'):
   code=[];i+=1
   while i<len(lines) and not lines[i].startswith('```'):code.append(lines[i]);i+=1
   out.append('<pre><code>'+html.escape('\n'.join(code))+'</code></pre>');i+=1;continue
  if s.startswith('|') and i+1<len(lines) and re.match(r'^\|[\s|:\-]+\|$',lines[i+1].strip()):
   cells=lambda r:r.strip().strip('|').split('|')
   out.append('<div class="table"><table><thead><tr>'+''.join('<th>'+inline(x.strip())+'</th>' for x in cells(s))+'</tr></thead><tbody>');i+=2
   while i<len(lines) and lines[i].strip().startswith('|'):
    out.append('<tr>'+''.join('<td>'+inline(x.strip())+'</td>' for x in cells(lines[i]))+'</tr>');i+=1
   out.append('</tbody></table></div>');continue
  if s.startswith('#'):
   n=len(s)-len(s.lstrip('#'));n=min(n+1,5);out.append(f'<h{n}>'+inline(s.lstrip('# '))+f'</h{n}>')
  elif s:out.append('<p>'+inline(s)+'</p>')
  i+=1
 return '\n'.join(out)
css='''*{box-sizing:border-box}body{margin:0;background:#f2f6fa;color:#20364a;font:15px/1.85 -apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC","Microsoft YaHei",sans-serif}main{max-width:1240px;margin:0 auto;padding:36px 24px}h1{font-size:32px;line-height:1.4}h2{margin-top:42px;border-bottom:1px solid #dce6ef;padding-bottom:12px}h3{margin-top:30px}p{margin:12px 0}a{color:#126d99}nav{display:flex;gap:20px;flex-wrap:wrap}section,.diagram{background:white;border:1px solid #dce6ef;border-radius:12px;padding:26px;margin:24px 0}.diagram svg{display:block;width:100%;height:auto}.table{overflow:auto}table{border-collapse:collapse;width:100%;font-size:13px}th,td{text-align:left;padding:12px;border-bottom:1px solid #e2eaf1;vertical-align:top;min-width:100px}th{background:#eaf2f8}pre{white-space:pre-wrap;background:#eff4f8;padding:20px;border-radius:8px}code{font-size:13px;overflow-wrap:anywhere}.notice{border-left:4px solid #c69536;padding:14px;background:#fff7e4}.phases{display:grid;grid-template-columns:repeat(7,1fr);gap:8px;margin-top:20px}.phase{background:#e7f1f8;padding:12px;border-radius:8px;font-size:12px}.phase b{display:block;font-size:19px}button{font:inherit;padding:6px 14px;background:white;border:1px solid #bdcfdd;border-radius:6px;cursor:pointer}@media(max-width:700px){main{padding:20px 12px}section,.diagram{padding:16px}.phases{grid-template-columns:repeat(2,1fr)}h1{font-size:26px}.diagram{overflow:auto}.diagram svg{min-width:850px}}@media print{body{background:white;font-size:11px}main{max-width:none;padding:0}nav,button{display:none}section,.diagram{border:0;padding:0}.diagram{break-inside:avoid}.table{overflow:visible}th,td{padding:6px;min-width:0}h2,h3{break-after:avoid}}'''
phases=['协议与边界','持久任务闭环','GPUStack与排空','容量与观测','部署与对外试用','长时商业试点','扩容与模型扩展']
page='<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>MaaS 统一模型服务框架 · 设计与路线图</title><style>'+css+'</style></head><body><main><h1>统一模型服务框架<br>设计 · 计划 · 路线图</h1><p>2026-10-20 · 设计提案 / 尚未实施</p><nav><a href="#architecture">组件架构图</a><a href="#design">详细设计</a><a href="#roadmap">实施路线图</a><a href="ARCHITECTURE.svg">独立 SVG 图</a><button onclick="window.print()">打印 / 保存 PDF</button></nav><p class="notice">保留 GPUStack 的 GPU 放置与模型部署能力；新增任务持久化、客户入口、节点排空和结果管理。001 必须常在线，worker 可退出。结果标记成功前保存到控制端。</p><div class="phases">'+''.join('<div class="phase"><b>F'+str(i)+'</b>'+v+'</div>' for i,v in enumerate(phases))+'</div><div class="diagram" id="architecture">'+svg+'</div><section id="design">'+render((P/'DESIGN.md').read_text())+'</section><section id="roadmap">'+render((P/'ROADMAP.md').read_text())+'</section></main></body></html>'
(P/'ROADMAP.html').write_text(page)
print(P/'ROADMAP.html')
