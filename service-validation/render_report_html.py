"""Generate self-contained HTML reports using the saved Markdown as source."""
import html,re
from pathlib import Path
BASE=Path(__file__).resolve().parents[1]/'results/ocr-service-v4'
def inline(s):
 s=html.escape(s)
 return re.sub(r'\[([^\]]+)\]\(([^)]+)\)',lambda m:'<a href="'+m[2].replace('DIRECTION.md','DIRECTION.html')+'">'+m[1]+'</a>',s)
def render(text):
 lines=text.splitlines();out=[];i=0;nav=[]
 while i<len(lines):
  line=lines[i].strip()
  if not line:i+=1;continue
  if line.startswith('|') and i+1<len(lines) and re.match(r'^\|[\s:|\-]+\|$',lines[i+1].strip()):
   cells=lambda x:[v.strip() for v in x.strip().strip('|').split('|')]
   headers=cells(line);out.append('<div class="table-wrap"><table><thead><tr>'+''.join('<th>'+inline(v)+'</th>' for v in headers)+'</tr></thead><tbody>');i+=2
   while i<len(lines) and lines[i].strip().startswith('|'):
    out.append('<tr>'+''.join('<td>'+inline(v)+'</td>' for v in cells(lines[i]))+'</tr>');i+=1
   out.append('</tbody></table></div>');continue
  if line.startswith('# '):out.append('<h1>'+inline(line[2:])+'</h1>');i+=1;continue
  if line.startswith('## '):
   ident='section-'+str(len(nav)+1);nav.append((ident,line[3:]));out.append('<h2 id="'+ident+'">'+inline(line[3:])+'</h2>');i+=1;continue
  if line.startswith('- '):
   out.append('<ul>')
   while i<len(lines) and lines[i].strip().startswith('- '):out.append('<li>'+inline(lines[i].strip()[2:])+'</li>');i+=1
   out.append('</ul>');continue
  out.append('<p>'+inline(line)+'</p>');i+=1
 return '\n'.join(out),nav
CSS='''*{box-sizing:border-box}html{scroll-behavior:smooth}body{margin:0;background:#f3f6fa;color:#1d2939;font:15px/1.85 -apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC","Microsoft YaHei",sans-serif}header{background:#122b46;color:white;padding:34px max(24px,calc((100vw - 1160px)/2))}header strong{font-size:13px;letter-spacing:2px;color:#a8d6ec}header p{margin:6px 0 0;color:#c7d9e9}main{max-width:1160px;margin:28px auto;padding:0 24px 64px}article{background:white;padding:36px 40px;border:1px solid #e0e7ef;border-radius:14px}h1{font-size:30px;line-height:1.4;margin:0 0 16px;color:#122b46}h2{font-size:21px;margin:40px 0 16px;padding-top:16px;border-top:1px solid #e6edf4;scroll-margin-top:16px}p{margin:12px 0}a{color:#086a9a;text-underline-offset:3px}.cards{display:grid;grid-template-columns:repeat(3,1fr);gap:16px;margin-bottom:24px}.card{padding:20px 24px;background:#fff;border:1px solid #dce6ef;border-radius:12px}.card b{display:block;font-size:30px;color:#126a85;line-height:1.5}.card span{color:#52677c;font-size:13px}nav{padding:16px 20px;background:#eff5f9;border-radius:8px;margin:24px 0}nav a{display:inline-block;margin:4px 18px 4px 0;font-size:13px}.table-wrap{overflow:auto;margin:20px 0;border:1px solid #dce5ee;border-radius:8px}table{border-collapse:collapse;width:100%;font-size:13px;white-space:nowrap;font-variant-numeric:tabular-nums}th{background:#eaf1f7;color:#284761;text-align:left}th,td{padding:12px 14px;border-bottom:1px solid #e8edf2}tbody tr:nth-child(even){background:#f7f9fc}tbody tr:last-child td{border:0}ul{padding-left:22px}li{margin:9px 0}.note{border-left:4px solid #d39b32;background:#fff8e9;padding:14px 18px;margin:20px 0}.footer{font-size:12px;color:#60758a;margin-top:24px}.actions{display:flex;gap:16px;flex-wrap:wrap;margin:18px 0}button{border:1px solid #bacbd9;background:white;color:#284761;padding:8px 14px;border-radius:6px;cursor:pointer;font:inherit} @media(max-width:700px){main{padding:0 12px 32px}article{padding:22px 18px}h1{font-size:24px}.cards{grid-template-columns:1fr}.card b{font-size:25px}header{padding:24px}.table-wrap{margin-left:-6px;margin-right:-6px}}@media print{body{background:white;font-size:11px}header,nav,.actions{display:none}main{max-width:none;margin:0;padding:0}article{border:0;padding:0}.cards{break-inside:avoid}.table-wrap{overflow:visible}table{white-space:normal;font-size:10px}th,td{padding:6px}h2{break-after:avoid}a{color:inherit}h1{font-size:24px}}'''
for filename in ['REPORT','DIRECTION']:
 text=(BASE/(filename+'.md')).read_text();body,nav=render(text);title=text.splitlines()[0].lstrip('# ')
 cards='<div class="cards"><div class="card"><span>方向与质量测试</span><b>872 次</b><span>含 192 次显式方向纠正</span></div><div class="card"><span>计时 HTTP 请求</span><b>9,799 / 9,799</b><span>全部成功；不代表识别准确率</span></div><div class="card"><span>已摆正模式持续吞吐</span><b>15.11 张/秒</b><span>两台 GPU · 四副本 · 并发 8</span></div></div>' if filename=='REPORT' else ''
 navigation='<nav aria-label="报告目录">'+''.join('<a href="#'+ident+'">'+inline(label)+'</a>' for ident,label in nav)+'</nav>'
 actions='<div class="actions"><button type="button" onclick="window.print()">打印 / 保存 PDF</button><a href="REPORT.html">主报告</a><a href="DIRECTION.html">方向实验详情</a><a href="service-phases.csv">下载阶段指标 CSV</a></div>'
 note='<div class="note">成本按 1 元/kWh、每天可用 8 小时，仅计 GPU 电费。日处理量为短时实测的满负载理论外推。</div>' if filename=='REPORT' else ''
 page='<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>'+html.escape(title)+'</title><style>'+CSS+'</style></head><body><header><strong>CYBERCAFE · OCR / MAAS</strong><p>GPUStack 双节点验证报告 · 2026-10-19</p></header><main>'+cards+'<article>'+actions+note+navigation+body+'<div class="footer">由同目录 Markdown 报告生成；页面样式内嵌，无需联网。分享方向详情及原始指标时，请同时保留对应文件。</div></article></main></body></html>'
 (BASE/(filename+'.html')).write_text(page,encoding='utf-8');print(BASE/(filename+'.html'))
