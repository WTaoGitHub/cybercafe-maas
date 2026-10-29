"""Render locally saved E3 completion evidence; no network or runtime mutation."""
import html,json,statistics
from pathlib import Path

ROOT=Path(__file__).resolve().parents[2]
OUT=ROOT/'results/maas-e3-completion-20261029'
NATIVE=['native-resume-check-20261029','native-resume-repeat-20261029']
ASYNC=['async4-final-r1-20261029','async4-final-r2-20261029','async4-steady-20261029',
       'async2-final-r1-20261029','async2-final-r2-20261029']


def read(path):return json.loads((OUT/path).read_text())
def samples(run):return [json.loads(x) for x in (OUT/run/'samples.jsonl').read_text().splitlines()]
def table(headers,rows):
    return '<div class="scroll"><table><thead><tr>'+''.join('<th>'+html.escape(str(x))+'</th>' for x in headers)+'</tr></thead><tbody>'+''.join('<tr>'+''.join('<td>'+html.escape(str(x))+'</td>' for x in row)+'</tr>' for row in rows)+'</tbody></table></div>'


def main():
    native=[read(run+'/status.json') for run in NATIVE]
    asynchronous=[read(run+'/status.json') for run in ASYNC]
    mixed=read('mixed-final-20261029/status.json');final=read('final.json')
    scales=[read('scale-to-2.json'),read('scale-to-4.json')]
    baseline=read('completion-20261029/baseline.json')
    assert all(r['phase']=='COMPLETE' for r in native+scales)
    assert all(r['state']=='COMPLETE' and not r['sample_errors'] for r in asynchronous)
    assert mixed['state']=='COMPLETE' and not mixed['sample_errors'] and mixed['max_combined_inflight']<=4
    assert mixed['mixed_overlap_samples']>0
    assert final['monitor_ok'] and not final['paused'] and final['unknown']==0
    assert final['model_hash']==baseline['model_hash'] and final['replicas']==4 and final['executors']==4
    points=[p for r in native for p in r['points']]
    assert len(points)==6 and all(p['phase']=='COMPLETE' and p['requests']==44 for p in points)
    assert all(row['state']=='SUCCEEDED' and row['attempts']==1 and row['success_events']==1 for run in asynchronous for row in run['database'])
    per_replica={str(n):statistics.mean(p['throughput_images_per_second'] for p in points if p['replicas']==n) for n in [4,8]}
    per_executor={str(n):statistics.mean(p['throughput_images_per_second'] for p in asynchronous if p['executors']==n and p['count']==40) for n in [2,4]}
    resources={str(n):{} for n in [4,8]}
    for n in [4,8]:
        selected=[r for run in NATIVE for r in samples(run) if r.get('phase')=='MEASURE' and r.get('point','').startswith('dual-'+str(n)+'-')]
        for worker in [7,8]:
            rows=[r['workers'][str(worker)] for r in selected]
            resources[str(n)][str(worker)]={'samples':len(rows),'observed_gpu_used_peak_gib':max(r['worker_node_gram_used_bytes'] for r in rows)/1024**3,
                'observed_cpu_peak_percent':max(r['worker_node_cpu_utilization_rate'] for r in rows),
                'observed_ram_peak_gib':max(r['worker_node_memory_used_bytes'] for r in rows)/1024**3,
                'observed_gpu_utilization_peak_percent':max(r['worker_node_gpu_utilization_rate'] for r in rows)}
    sync_ok=sum(r['kind']=='sync' and r.get('http_status')==200 for r in mixed['records'])
    sync_429=sum(r['kind']=='sync' and r.get('http_status')==429 for r in mixed['records'])
    steady=next(r for r in asynchronous if r['count']==720)
    summary={'status':'PILOT_ACCEPTED','native_points':len(points),'native_measured_requests':sum(p['requests'] for p in points),
        'native_warmup_and_preflight_requests':sum(len(p['warmup_records']) for p in points)+len(native),
        'async_successes_including_warmup':sum(len(r['database']) for r in asynchronous)+len(mixed['tasks']),
        'sync_accepted':sync_ok,'sync_capacity_rejected':sync_429,'mixed_overlap_samples':mixed['mixed_overlap_samples'],
        'max_mixed_inflight':mixed['max_combined_inflight'],'native_mean_by_replicas':per_replica,'async_mean_by_executors':per_executor,
        'native_8_vs_4_difference_percent':100*(per_replica['8']/per_replica['4']-1),
        'observed_native_resources':resources,'steady_seconds':steady['elapsed_seconds'],'steady_throughput':steady['throughput_images_per_second'],
        'observed_db_lock_waiters_peak':max(row['tasks']['lock_waiters'] for run in ASYNC for row in samples(run)),
        'recommendation':{'model_replicas':4,'executors':4,'global_limit':4,'project_limit':4,'vram_claim_bytes':2147483648},
        'final':final,'boundaries':['No new eight-hour or production SLA validation','No lower VRAM claim experiment','No power-cost or per-container CPU/RAM allocation result','No multi-host artifact or thousand-node proof','GW issue remains under observation']}
    (OUT/'summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2)+'\n')
    native_rows=[[r['run_id'],p['name'],p['replicas'],p['requests'],f"{p['throughput_images_per_second']:.3f}",f"{p['p95_seconds']:.3f}",f"{p['process_start_seconds']:.2f}"] for r in native for p in r['points']]
    async_rows=[[r['run_id'],r['executors'],r['count'],len(r['database']),f"{r['throughput_images_per_second']:.3f}",f"{r['e2e_p95_seconds']:.3f}"] for r in asynchronous]
    resource_rows=[[n,w,v['samples'],f"{v['observed_gpu_used_peak_gib']:.3f}",f"{v['observed_ram_peak_gib']:.2f}",f"{v['observed_cpu_peak_percent']:.1f}",f"{v['observed_gpu_utilization_peak_percent']:.1f}"] for n,workers in resources.items() for w,v in workers.items()]
    conclusion=f"本次受控试点E3验收通过。保留4个OCR副本（两卡各2）、4个执行器、全局/project额度4及每副本2GiB申报。原生八副本相对四副本三轮均值增加{summary['native_8_vs_4_difference_percent']:.1f}%，未显示足以支持默认翻倍副本的明确收益；该小样本不是统计显著性或生产SLA证明。"
    limits="720张稳态只覆盖约%.1f秒，不是新的8小时测试。输入为固定40图；原生矩阵另含4张3MP/9MP PNG/JPEG。结果验证为协议/结果可读和事件一致性，不是新的CER盲测。显存为原生指标低频整卡观察下界，不能除以副本数作为实例安全峰值；未量化GPU电耗、每实例CPU/RAM硬限额或文件锁精细等待。更低claim、每卡7/8副本及异构/更大规模不据此视为通过。"%steady['elapsed_seconds']
    ops="扩缩容4→2→4均先暂停排空、保留统一额度、只调整执行器，再以真实46行OCR探针恢复；模型配置哈希不变。结束时generation %s、UNKNOWN=0、监控正常、接单已恢复。未重启VM、Docker引擎或GPUStack worker；未恢复collector。"%final['generation']
    mixed_text=f"24个异步任务全部成功、attempt=1、成功事件各1；36次同步尝试中{sync_ok}次成功、{sync_429}次明确SYNC_CAPACITY/429且未创建成功事件。观测{mixed['mixed_overlap_samples']}个同步/异步重叠样本，组合在途最大{mixed['max_combined_inflight']}，未超过全局及该project额度4。同步计量使用空闲窗口token差集，不是持久请求ID映射；本窗口的其他同步流量会使对账失败。"
    sections=[('部署与结论','<p>'+html.escape(conclusion)+'</p>'),
        ('原生GPUStack副本矩阵',table(['Run','点位','总副本','测量请求','张/秒','P95秒','进程启动秒'],native_rows)),
        ('MaaS异步端到端',table(['Run','执行器','测量张数','含预热成功/事件数','张/秒','P95秒'],async_rows)+'<p>端到端包含领取、排队、鉴权、轮询及结果下载。不同链路不能直接与原生表作同口径比较；新旧拓扑结果分开记录。数据库采样观测到锁等待者峰值'+str(summary['observed_db_lock_waiters_peak'])+'，未出现采样异常；这不是锁等待时长或无竞争证明。</p>'),
        ('同步/异步统一额度竞争','<p>'+html.escape(mixed_text)+'</p>'),
        ('节点资源观测',table(['总副本','Worker ID','测量期样本数','整卡显存峰值GiB','整机RAM峰值GiB','CPU峰值%','GPU利用率峰值%'],resource_rows)+'<p>均为节点级原生指标的观测峰值，不是单实例资源额度；CPU利用率不能换算为专用核数。</p>'),
        ('受控扩缩容与最终状态','<p>'+html.escape(ops)+'</p>'),
        ('验收边界与后续','<p>'+html.escape(limits)+'</p><p>GW-20261028-001继续开放观察，原始503触发因果未完全证实。原失败与采样中断记录均保留；E10参数优化、E8独立版本适配及E7规模验证各自验收，本轮不自动启动。</p>')]
    document='''<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>E3完成验证 · 2026-10-29</title><style>body{margin:0;background:#f2f5f8;color:#172b42;font:16px/1.7 system-ui,sans-serif}main{max-width:1160px;margin:auto;padding:28px 24px}h1{font-size:30px}section,.success{background:white;border-radius:12px;padding:22px;margin:20px 0}.success{border-left:6px solid #278658;background:#effaf4}h2{font-size:22px;margin-top:0}.scroll{overflow:auto}table{width:100%;border-collapse:collapse;font-size:14px}th,td{text-align:left;padding:10px;border-bottom:1px solid #dce3eb}th{background:#edf2f8}a{color:#155ba0}.note{color:#53657a}</style><main><h1>E3并发与容量 · 完成验证</h1><p class="note">2026-10-29 · 两GPU受控试点 · 证据与未测范围分开记录</p><div class="success"><strong>本轮试点验收通过，已恢复接单。</strong><br>原生测量264次；异步含预热与混合测试共'''+str(summary['async_successes_including_warmup'])+'个任务成功且唯一计量。</div>'
    document+=''.join('<section><h2>'+title+'</h2>'+body+'</section>' for title,body in sections)
    document+='<p><a href="REPORT.md">详细报告</a> · <a href="summary.json">汇总</a> · <a href="final.json">最终状态</a></p></main></html>'
    (OUT/'REPORT.html').write_text(document)
    md='# E3并发与容量完成验证\n\n2026-10-29。'+conclusion+'\n\n'
    for title,headers,rows in [('原生副本矩阵',['Run','点位','副本','请求','张/秒','P95秒','启动秒'],native_rows),('异步端到端',['Run','执行器','张数','含预热成功任务/事件','张/秒','P95秒'],async_rows),('节点资源观测',['总副本','Worker','样本','整卡显存GiB','整机RAM GiB','CPU峰值%','GPU利用率峰值%'],resource_rows)]:
        md+='## '+title+'\n\n| '+' | '.join(headers)+' |\n|'+ '|'.join(['---']*len(headers))+'|\n'
        md+=''.join('| '+' | '.join(map(str,row))+' |\n' for row in rows)+'\n'
    md+='整卡显存/整机RAM不是单实例峰值或硬限额；CPU利用率不等于专用核数。数据库采样观测锁等待者峰值'+str(summary['observed_db_lock_waiters_peak'])+'，未精细量化等待时长。\n\n'
    md+='## 统一额度与计量\n\n'+mixed_text+'\n\n## 扩缩容与恢复\n\n'+ops+'\n\n## 已验证范围与未测项\n\n'+limits+'\n\n'
    md+='当前仅完成两GPU试点的E3范围；保留2GiB及4副本是有测量依据的阶段结论。单卡历史1/2/4矩阵、独立库286项及新工具30项回归为相关证据，未重复停库或VM级实验。当前工具未新增跨主机存储/调度器或后端合批。GW问题继续观察，E10/E8/E7不自动启动。\n\n'
    md+='## 证据\n\n[汇总](summary.json)、[开始基线](completion-20261029/baseline.json)、[最终状态](final.json)、[缩容](scale-to-2.json)、[扩容](scale-to-4.json)、[混合测试](mixed-final-20261029/status.json)。每个run目录保存原始status.json及samples.jsonl。原生请求不经过MaaS成功计量；预热/预检与正式测量分开统计。模型完整配置及env备份仅在Mac私有目录保留。\n'
    (OUT/'REPORT.md').write_text(md)
    print(json.dumps({k:summary[k] for k in ['status','native_measured_requests','async_successes_including_warmup','sync_accepted','sync_capacity_rejected','steady_seconds']}))


if __name__=='__main__':main()
