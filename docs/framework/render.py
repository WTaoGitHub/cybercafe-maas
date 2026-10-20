"""Rebuild v0.2 design overview and self-contained component SVG; stdlib only."""
from pathlib import Path
import html,re
P=Path(__file__).resolve().parent

def inline(s):
    s=html.escape(s)
    s=re.sub(r'`([^`]+)`',r'<code>\1</code>',s)
    s=re.sub(r'\*\*([^*]+)\*\*',r'<strong>\1</strong>',s)
    return re.sub(r'\[([^\]]+)\]\(([^)]+)\)',lambda m:'<a href="'+m[2]+'">'+m[1]+'</a>',s)

def render(text):
    lines=text.splitlines();out=[];i=0
    while i<len(lines):
        s=lines[i].strip()
        if not s:i+=1;continue
        if s.startswith('```'):
            code=[];i+=1
            while i<len(lines) and not lines[i].startswith('```'):code.append(lines[i]);i+=1
            out.append('<pre><code>'+html.escape('\n'.join(code))+'</code></pre>');i+=1;continue
        if s.startswith('|') and i+1<len(lines) and re.match(r'^\|[\s|:\-]+\|$',lines[i+1].strip()):
            cells=lambda row:row.strip().strip('|').split('|')
            out.append('<div class="table"><table><thead><tr>'+''.join('<th>'+inline(c.strip())+'</th>' for c in cells(s))+'</tr></thead><tbody>');i+=2
            while i<len(lines) and lines[i].strip().startswith('|'):
                out.append('<tr>'+''.join('<td>'+inline(c.strip())+'</td>' for c in cells(lines[i]))+'</tr>');i+=1
            out.append('</tbody></table></div>');continue
        if s.startswith('#'):
            n=min(len(s)-len(s.lstrip('#'))+1,5);out.append(f'<h{n}>'+inline(s.lstrip('# '))+f'</h{n}>');i+=1;continue
        if s.startswith('- ') or re.match(r'^\d+\. ',s):
            ordered=not s.startswith('- ');tag='ol' if ordered else 'ul';out.append('<'+tag+'>')
            while i<len(lines):
                row=lines[i].strip();match=re.match(r'^\d+\. (.*)',row) if ordered else re.match(r'^- (.*)',row)
                if not match:break
                out.append('<li>'+inline(match[1])+'</li>');i+=1
            out.append('</'+tag+'>');continue
        if s.startswith('>'):out.append('<blockquote>'+inline(s.lstrip('> '))+'</blockquote>')
        else:out.append('<p>'+inline(s)+'</p>')
        i+=1
    return '\n'.join(out)

CSS='''*{box-sizing:border-box}html{scroll-behavior:smooth}body{margin:0;background:#f2f6fa;color:#20364a;font:15px/1.85 -apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC","Microsoft YaHei",sans-serif}main{max-width:1380px;margin:0 auto;padding:32px 24px 70px}h1{font-size:32px;line-height:1.4}h2{margin-top:30px;border-bottom:1px solid #dce6ef;padding-bottom:12px}h3{margin-top:26px}a{color:#126d99}nav{display:flex;gap:20px;flex-wrap:wrap}section,.diagram{background:white;border:1px solid #dce6ef;border-radius:12px;padding:26px;margin:24px 0}.diagram svg{display:block;width:100%;height:auto}.table{overflow:auto}table{border-collapse:collapse;width:100%;font-size:13px}th,td{text-align:left;padding:12px;border-bottom:1px solid #e2eaf1;vertical-align:top;min-width:110px}th{background:#eaf2f8}pre{white-space:pre-wrap;background:#eff4f8;padding:20px;border-radius:8px}code{font-size:13px;overflow-wrap:anywhere}.notice,blockquote{border-left:4px solid #b48b34;padding:14px 18px;background:#fff7e4;margin:18px 0}.phases{display:grid;grid-template-columns:repeat(5,1fr);gap:10px;margin:22px 0}.phase{background:#e2edf7;padding:15px;border-radius:8px;font-size:13px}.phase b{display:block;font-size:22px}button{font:inherit;padding:6px 14px;background:white;border:1px solid #bdcfdd;border-radius:6px;cursor:pointer}@media(max-width:700px){main{padding:18px 12px}section,.diagram{padding:16px}.phases{grid-template-columns:repeat(2,1fr)}h1{font-size:26px}.diagram{overflow:auto}.diagram svg{min-width:1000px}}@media print{body{background:white;font-size:11px}main{max-width:none;padding:0}nav,button{display:none}section,.diagram{border:0;padding:0}.diagram{break-inside:avoid}.table{overflow:visible}th,td{padding:6px;min-width:0}h2,h3{break-after:avoid}}'''

def svg_diagram():
    s=['<svg xmlns="http://www.w3.org/2000/svg" width="1440" height="1120" viewBox="0 0 1440 1120" role="img" aria-labelledby="title desc"><title id="title">统一模型服务架构 v0.2</title><desc id="desc">固定GPUStack v2.2.3，客户同步请求直接走GPUStack；异步请求经任务API、独立PostgreSQL与执行器，再调用GPUStack。输入与权威结果保存在001控制端。</desc><defs><marker id="arrow" markerWidth="8" markerHeight="8" refX="7" refY="4" orient="auto"><path d="M0 0 L8 4 L0 8" fill="#42657e"/></marker></defs><style>text{font-family:-apple-system,BlinkMacSystemFont,Segoe UI,PingFang SC,Microsoft YaHei,sans-serif;fill:#203d55}.t{font-size:17px;font-weight:650}.d{font-size:14px;fill:#506e84}.l{font-size:14px;fill:#265c7d}</style><rect width="1440" height="1120" fill="#f7fafd"/><text x="36" y="40" font-size="27" font-weight="700">统一模型服务架构 v0.2</text><text x="36" y="69" class="d">固定 GPUStack v2.2.3 + 最小持久任务层 · 2026-10-20 · 目标架构 · P0部分/P1模拟闭环已验证，其余待实施</text>']
    def box(x,y,w,h,title,sub='',fill='white'):
        s.append(f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="10" fill="{fill}" stroke="#b6cbdc"/><text x="{x+w/2}" y="{y+29}" text-anchor="middle" class="t">{html.escape(title)}</text>')
        if sub:s.append(f'<text x="{x+w/2}" y="{y+53}" text-anchor="middle" class="d">{html.escape(sub)}</text>')
    def path(points,label='',lx=0,ly=0,dash=False):
        s.append('<path d="'+points+'" fill="none" stroke="#42657e" stroke-width="1.8" marker-end="url(#arrow)"'+(' stroke-dasharray="7 5"' if dash else '')+'/>')
        if label:s.append(f'<text x="{lx}" y="{ly}" class="l">{html.escape(label)}</text>')
    box(525,95,390,70,'客户 / SDK','同一已登记的 GPUStack inference key')
    box(525,205,390,70,'统一边缘入口','HTTPS / 路径分流 / 体积与连接限制')
    path('M720 165 V205')
    s.append('<rect x="25" y="325" width="1390" height="505" rx="16" fill="#eef3f8" stroke="#c6d4df"/><text x="45" y="354" class="t">001 常在线控制端 · 保留 GPU worker，控制端不能随节点切换系统</text>')
    s.append('<rect x="45" y="375" width="805" height="410" rx="12" fill="#e6f0fa" stroke="#a9c6df"/><text x="65" y="404" class="t">新增薄任务层 · 一个代码库，API 与执行器两个进程角色</text>')
    s.append('<rect x="885" y="375" width="510" height="410" rx="12" fill="#e3f1e9" stroke="#abcdb7"/><text x="905" y="404" class="t">复用 GPUStack v2.2.3</text>')
    box(80,435,330,75,'任务 API','上传 / 提交 / 幂等 / 查询 / 取消')
    box(500,435,300,75,'身份桥接 AuthConnector','登记key映射 + 上游鉴权 / ACL')
    box(80,565,330,75,'独立 PostgreSQL','持久队列 / attempt / 租约 / 用量','#fff7df')
    box(500,565,300,75,'执行器 + 协调循环','领取 / 续租 / 有限重试 / 维护')
    box(80,695,330,70,'ArtifactStore 持久卷','输入 / 权威结果 / 鉴权下载 / TTL','#fff7df')
    s.append('<text x="500" y="715" class="d">结果模块属于任务层，不拆微服务。</text><text x="500" y="740" class="d">成功前必须完成控制端持久提交。</text>')
    box(920,435,440,75,'GPUStack 推理入口','API key / 模型ACL / 路由 / 同步 / SSE')
    box(920,565,440,75,'GPUStack 部署与资源管理','模型目录 / backend / GPU放置 / 副本')
    box(920,695,440,70,'已有用量与监控','worker健康 / GPU指标 / 基础usage')
    path('M610 275 V305 H245 V435','异步 /maas/v1：提交、查询、下载',65,299)
    path('M835 275 V305 H1140 V435','同步 / 流式：既有推理路径',980,299)
    path('M410 472 H500','验证身份',422,458)
    path('M800 472 H920','原生验证',812,458,True)
    path('M245 510 V565','事务受理202',92,548)
    path('M500 602 H410','领取 / 续租',418,589)
    path('M800 602 H865 V530 H1140 V510','异步任务内部调用',897,549)
    path('M245 640 V695','元数据 / 结果引用',83,681)
    path('M580 640 V665 H430 V730 H410','写入并原子提交结果',455,687)
    s.append('<rect x="25" y="880" width="1390" height="145" rx="16" fill="#f1f6f2" stroke="#c2d7c9"/><text x="45" y="909" class="t">GPU worker 资源池 · 只有适配器包含模型算法，worker 可保留临时缓存</text>')
    box(70,930,370,70,'001 worker','模型适配器 → GPU实例')
    box(535,930,370,70,'002 worker','模型适配器 → GPU实例')
    box(1000,930,370,70,'后续 worker','按相同接入规范扩容')
    path('M1360 472 H1380 V855 H720 V930','推理与结果返回',1125,846)
    path('M1140 640 V665 H1400 V865 H255 V930','部署 / 副本 / 健康管理',62,854,True)
    s.append('<text x="36" y="1064" class="d">首期维护：全局暂停派发与同步旁路 → 核对在途/未知执行 → maintenance → 恢复探测；不是精确逐节点无损 drain。</text><text x="36" y="1092" class="d">实线：调用 / 数据　虚线：控制 / 验证　黄色：新增持久存储　绿色：复用已有底座　无 main 专属能力依赖。</text></svg>')
    return ''.join(s)

def standalone(source):
    text=(P/source).read_text();title=text.splitlines()[0].lstrip('# ')
    dest=P/(Path(source).stem+'.html')
    dest.write_text('<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>'+html.escape(title)+'</title><style>'+CSS+'</style></head><body><main><nav><a href="ROADMAP.html">当前设计与路线图</a></nav><section>'+render(text)+'</section></main></body></html>')

if __name__=='__main__':
    svg=svg_diagram();(P/'ARCHITECTURE.svg').write_text(svg)
    phases=['复用与协议确认','最小持久任务闭环','真实模型与恢复','受控维护与运营边界','发布和长时验收']
    header='<h1>统一模型服务框架 v0.2<br>GPUStack v2.2.3 + 最小持久任务层</h1><p>2026-10-20 · 当前设计基线 / P0部分及P1模拟闭环已验证</p><nav><a href="#architecture">组件架构</a><a href="#design">详细设计</a><a href="#plan">实施计划</a><a href="#roadmap">路线图</a><a href="ARCHITECTURE.svg">独立 SVG</a><button onclick="window.print()">打印 / 保存 PDF</button></nav><p class="notice">复用身份、模型目录、路由、同步/SSE、部署与监控；新增任务API、执行器、独立PostgreSQL及控制端结果卷。固定v2.2.3，不依赖main，不承诺逐节点无损排空。</p>'
    stages='<div class="phases">'+''.join('<div class="phase"><b>P'+str(i)+'</b>'+v+'</div>' for i,v in enumerate(phases))+'</div>'
    sections=''.join('<section id="'+ident+'">'+render((P/file).read_text())+'</section>' for ident,file in [('design','DESIGN.md'),('plan','PLAN.md'),('roadmap','ROADMAP.md')])
    (P/'ROADMAP.html').write_text('<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>MaaS框架v0.2 · 设计、计划与路线图</title><style>'+CSS+'</style></head><body><main>'+header+stages+'<div class="diagram" id="architecture">'+svg+'</div>'+sections+'</main></body></html>')
    for name in ['GPUSTACK-V2.2.3-SCOPE.md','GPUSTACK-REUSE-REVIEW.md']:
        if (P/name).exists():standalone(name)
    import sys,subprocess
    if "--png" in sys.argv:
        subprocess.run(["sips","-s","format","png",str(P/"ARCHITECTURE.svg"),"--out",str(P/"ARCHITECTURE.png")],check=True)
    print(P/'ROADMAP.html')
