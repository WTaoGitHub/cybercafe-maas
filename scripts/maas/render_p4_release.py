"""Render saved evidence; eight-hour gate cannot pass from a running/short snapshot."""
import html,json,re,math
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo
ROOT=Path(__file__).resolve().parents[2];BASE=ROOT/'results/maas-p4-release'


def load(name):return json.loads((BASE/name).read_text())
def local(ts):return datetime.fromtimestamp(ts,ZoneInfo('Asia/Shanghai')).strftime('%Y-%m-%d %H:%M:%S +08:00')
def eight_hour_passed(end,verification):
 return (end.get('state')=='COMPLETE' and math.isfinite(end.get('elapsed_seconds',0))
  and end.get('elapsed_seconds',0)>=28800 and end.get('duration_target_seconds',0)>=28800
  and end.get('accepted',0)>=max(1,end.get('fixtures',1)) and end.get('accepted')==end.get('succeeded') and not end.get('errors')
  and end.get('database_check')=={'tasks':end.get('accepted'),'succeeded':end.get('succeeded'),'usage_events':end.get('succeeded')}
  and verification.get('passed') is True and verification.get('run_id')==end.get('run_id')
  and verification.get('target_seconds')==end.get('duration_target_seconds'))


def main():
 smoke=load('endurance-smoke.json');four=load('four-client.json');rest=load('restore-result.json');restart=load('docker-restart-window.json');rollback=load('rollback.json')
 retest=any((BASE/name).exists() for name in ('endurance-retest-final.json','endurance-retest-progress.json'))
 end=load(('endurance-retest-final.json' if (BASE/'endurance-retest-final.json').exists() else 'endurance-retest-progress.json') if retest else ('endurance-final.json' if (BASE/'endurance-final.json').exists() else 'endurance-progress.json'))
 vfile='endurance-retest-verification.json' if retest else 'endurance-verification.json'
 verification=load(vfile) if (BASE/vfile).exists() else {}
 passed=eight_hour_passed(end,verification)
 failure_note='## 夜间失败与排查\n\n本轮于北京时间2026-10-21 22:16:38提前结束：2360接纳、2359成功、1个PREFLIGHT_UNAVAILABLE。collector采集失败与002 SSH MaxStartups限流时间吻合，因果链仍需专项验证。首次故障后服务曾恢复，不自动重启负载，P4未通过，E5不启动。详见[失败记录与修复建议](ENDURANCE-FAILURE.md)。\n\n' if end.get('state')=='FAILED' and end.get('run_id')=='night-8h-20261021' and (BASE/'ENDURANCE-FAILURE.md').exists() else ''
 if retest:
  failure_note='## E6整改后的独立复测\n\n自建collector已退役，当前复测使用新run ID，不拼接旧时长。原night-8h-20261021保持FAILED，见[原失败记录](ENDURANCE-FAILURE.md)。E6实机维护/恢复与备份证据见[执行记录](../../docs/framework/E6-RETIREMENT.md)。\n\n'
 if not retest and failure_note and (BASE/'monitor-recurrence-20261022.json').exists():
  failure_note+='复发更新：2026-10-22 02:58快照显示collector再次失败，独立监控COLLECTOR_FAILED_OR_STALE，UNKNOWN=0；002同窗反复SSH MaxStartups限流。见失败记录中的复发证据。\n\n'
 assert smoke['state']=='COMPLETE' and restart['ok'] and rest['ok'] and rollback['ok']
 status='8小时门槛通过；其余验收按本报告的私有两节点范围解释。' if passed else ('耐久测试失败或中断，P4未通过，请检查原始状态及错误记录。' if end.get('state') in ('FAILED','INTERRUPTED') else '8小时耐久测试尚未完成或最终核对未通过，P4暂不整体验收。')
 text=f'''# P4 发布与耐久验收进展

{status}

当前范围：可信两节点、GPUStack v2.2.3、001单执行器及受控私有入口。用户明确暂不开放公网；不实现E1/E2/E3或推理后端合批。

## 已完成

| 任务 | 实测结果 |
|---|---|
| 私有HTTPS | 001回环18832，自签证书90天；信任指定证书后OCR200/46行；未信任证书时拒绝连接，未使用跳过证书校验 |
| 独立监控 | 001 systemd每30秒检查服务、TLS与observer新鲜度；E6后不再依赖自建collector；历史observer停止/恢复告警已验证，无外部通知通道 |
| 指标历史与看板 | 私有聚合历史轮换、最多约10MiB；最近120个采样的任务速率和资源指标；HTML是导出快照 |
| 备份 | 暂停并排空、停止三个应用写入进程后同时备份任务DB/artifact/应用配置；正式数据不删除 |
| 加密异机副本 | 本机.maas保存AES-256-CBC/PBKDF2加密文件、独立HMAC-SHA256验证；密钥不离开本机、不入Git；篡改密文在回传前被拒绝 |
| 独立恢复 | 从加密副本解密回传，恢复到独立_test数据库和目录；核对{rest['successful_task_events']}条成功记录、{rest['artifacts_verified']}个文件及凭据读取权限；正式库不覆盖，临时恢复副本清理 |
| 数据恢复耗时 | {rest['restore_seconds']}秒，仅本次已备好测试环境的数据恢复/核对阶段，不是整机恢复RTO；备份为手动快照，未承诺生产RPO |
| 网络持久化 | 两节点在Docker前安装自有raw/pre-DNAT拦截，再对运行期Docker清单刷新；不清空全局规则，保留SSH |
| Docker重启 | 全局维护STOPPED期间重启两个现有Docker引擎；001抽样{restart['node001']['raw_fence_samples']}次、002抽样{restart['node002']['raw_fence_samples']}次，缺失拦截样本均0；恢复到RESUMED |
| 外部隔离复测 | 两节点隔离netns验证IPv4、IPv6和直连容器目标均不可达，SSH仍可达；无新worker/daemon |
| 应用回退 | 不同内容镜像p4-r1→p4-r2→p4-r1；暂停/队列和原卷保留，探针后任务一次成功；未做schema降级，不代表任意未来版本兼容 |
| 单次4客户端提交 | 4个任务均成功；提交P95（nearest-rank，4个样本）{four['submission_p95_ms_nearest_rank']:.2f}毫秒，低于工程基线1000毫秒；不是持续并发SLA |
| 回归 | 131通过、1跳过；实际DB中断专项本轮未重复 |

## 长时测试

- 服务：{end.get("service", "maas-endurance-e6-night-20261022" if retest else "maas-endurance-night-20261021")}；run ID：{end["run_id"]}。
- 白天一轮按用户要求主动停止，1276.26秒、256/256成功、无错误；不计为8小时通过。
- 本轮计划：{local(end["started_at"])}至{local(end["started_at"]+28800)}，由服务器systemd执行，不依赖本机在线；错过定时窗口不自动补跑。
- 起点（SCHEDULED时为计划时间）：{local(end['started_at'])}；预计最早完成：{local(end['started_at']+28800)}。
- 40张固定中英文样本（DocLayNet/RCTW-17各20），每5秒提交一个独立异步任务，经校验证书的HTTPS完成上传/提交/查询/下载。
- 单执行器、每项目额度1保持不变；这验证持续运行与持久性，不测最大吞吐或后端合批。
- 预检短测：{smoke['accepted']}提交/{smoke['succeeded']}成功，零错误；成功计量逐任务对账一致；端到端P95 {smoke['e2e_p95_seconds']:.3f}秒。
- 本地保存快照状态：{end['state']}；已运行{end.get('elapsed_seconds',0):.2f}秒，{end.get('accepted',0)}提交/{end.get('succeeded',0)}成功。此项是快照，后续由验收跟进更新。
- 8小时结束必须核对：elapsed≥28800、所有accepted有成功终态、errors为空、数据库任务/成功事件数量一致，且verify_endurance.py确认请求时间连续（最大间隔不超过60秒），不能用休眠满8小时代替持续负载；异常时停止负载且不自动解除UNKNOWN或重启计时。
- 已安排每小时跟进，进度正常时不通知；完成或发生需处理的问题再汇总。

{failure_note}## 保留边界

没有实际整机重启/VM断电或Windows切换；验证了systemd依赖与两次Docker引擎重启的拦截抽样。监控与控制端同机，不能覆盖001整机失联，不提供HA。私有TLS不代表已开放公网或取得公有CA证书。备份包含敏感配置，归档与恢复密钥不得提交Git，需另行安全保管密钥；尚无生产备份周期/RPO约定。P4是否通过仍以长时结果及上述范围为准。

## 原始证据

[私有TLS/监控](tls-monitor.json) · [指标看板快照](monitor-dashboard.html) · [备份记录](backup-status.json) · [恢复核对](restore-result.json) · [密文篡改反例](backup-authentication.json) · [Docker重启](docker-restart-window.json) · [001隔离探测](boundary-001-probes.json) · [002隔离探测](boundary-002-probes.json) · [镜像回退](rollback.json) · [四客户端提交](four-client.json) · [短测](endurance-smoke.json) · [原8小时快照](endurance-progress.json) · [E6复测快照](endurance-retest-progress.json) · [原服务核对](final-verification.json) · [回归日志](regression.log) · [运行收尾状态](post-release-state.json)
'''
 (BASE/'REPORT.md').write_text(text)
 lines=text.splitlines();out=[];i=0
 def inline(s):return re.sub(r'\[([^\]]+)\]\(([^)]+)\)',r'<a href="\2">\1</a>',html.escape(s))
 while i<len(lines):
  s=lines[i]
  if s.startswith('|') and i+1<len(lines) and re.match(r'^\|[-| ]+\|$',lines[i+1]):
   cells=lambda x:[inline(t.strip()) for t in x.strip('|').split('|')]
   out.append('<div class="table"><table><tr>'+''.join('<th>'+x+'</th>' for x in cells(s))+'</tr>');i+=2
   while i<len(lines) and lines[i].startswith('|'):
    out.append('<tr>'+''.join('<td>'+x+'</td>' for x in cells(lines[i]))+'</tr>');i+=1
   out.append('</table></div>');continue
  if s.startswith('## '):out.append('<h2>'+inline(s[3:])+'</h2>')
  elif s.startswith('# '):out.append('<h1>'+inline(s[2:])+'</h1>')
  elif s.startswith('- '):out.append('<p>• '+inline(s[2:])+'</p>')
  elif s:out.append('<p>'+inline(s)+'</p>')
  i+=1
 css='*{box-sizing:border-box}body{background:#eef4f8;color:#203448;font:16px/1.85 system-ui;margin:0}main{max-width:1120px;margin:auto;padding:28px 22px 60px}article{background:white;padding:28px;border:1px solid #dce5ed;border-radius:14px}h1{font-size:29px}h2{font-size:21px;border-top:1px solid #dce5ed;padding-top:20px}a{color:#086a99}.table{overflow:auto}table{border-collapse:collapse;width:100%;font-size:14px}th,td{padding:10px;text-align:left;border-bottom:1px solid #dce5ed;min-width:150px}th{background:#edf3f8}.notice{padding:18px;background:#fff3d9;border-radius:12px;margin-bottom:20px}@media(max-width:650px){main{padding:16px 12px}article{padding:18px}h1{font-size:24px}}@media print{body{background:white}article{padding:0;border:0}}'
 (BASE/'REPORT.html').write_text('<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>P4发布与耐久验收</title><style>'+css+'</style></head><body><main><div class="notice">'+inline(status)+'</div><article>'+''.join(out)+'</article></main></body></html>')
 print(json.dumps({'eight_hour_gate_passed':passed,'run_state':end['state'],'report':str(BASE/'REPORT.html')}))


if __name__=='__main__':main()
