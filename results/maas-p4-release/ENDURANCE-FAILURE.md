# 夜间8小时验收失败记录

2026-10-21夜间run：night-8h-20261021。**P4未通过8小时门槛，不进入E5。**

## 已确认事实

- 北京时间19:00:02开始，22:16:38结束，持续11796.06秒（约3小时16分钟36秒），未达到28800秒。
- 2360个已接纳任务，2359成功、1个FAILED/PREFLIGHT_UNAVAILABLE；2359个唯一成功计量事件，未观察到计量数量差异。
- SQL核对：请求覆盖11795.289157秒，最大间隔5.21252秒；在失败前持续负载符合原5秒节奏，但不足8小时且存在失败，因此verification.passed=false。
- 成功请求端到端P95约1.154秒，不能将该条件分布解释为含失败的整体SLA或最大吞吐。
- collector在22:16:34—22:19:28记录16次COLLECTION_UNAVAILABLE，失败时撤销可信预检证明；任务因此在推理前检查阶段失败，未盲目重放。
- 002 sshd在22:16:21开始MaxStartups限流，22:19:38结束，日志记录932个连接丢弃。该窗口有外部来源未认证连接突增；随后001内网清单连接再次成功。
- 后续检查：两worker ready，原两模型四实例真实OCR均200/46行；collector健康，监控无告警，异步/同步UNKNOWN为0，未暂停。两节点raw边界hook在位，001未见H3/cloudflared运行；002当次运行容器清单也无H3/隧道。
- 没有修改节点、模型、SSH配置或预检规则；没有重新计时、重放失败任务或启动新负载。

## 初步判断与尚未证明部分

最可能链路：外部连接压力触发002共享sshd的全局MaxStartups限制，collector每轮新建SSH清单连接受影响，导致采集失败并撤销preflight，执行器返回PREFLIGHT_UNAVAILABLE。时间吻合、sshd与collector日志可相互支持。

但collector当前只保存通用COLLECTION_UNAVAILABLE码，没有记录当轮失败阶段和安全分类后的stderr，因此不能把上述相关性当作已完整复现的唯一根因，也没有证据表明外部连接成功登录。OCR容器当次显示长时间运行，未观察到该窗口的容器重建；Docker事件历史无输出不是绝对无重启证明。

额外发现：runner状态FAILED但进程退出0/systemd Result=success；业务验收必须读取status/verification，不能单凭unit退出状态宣告测试通过。

## 建议修复/复测顺序

1. 先补collector安全诊断：失败阶段、稳定错误分类与时间，保留凭据/模型配置脱敏，不打印原始管理响应或秘密。
2. 在隔离环境验证SSH并发压力与collector失败的因果关系，避免在正式节点复现公网连接洪峰。
3. 优先评估将只读清单通道与公网SSH未认证连接压力隔离（独立内网受限监听/进程、专用身份/来源限制），或复用经校验的持久连接；必须保留forced-command、主机密钥及网络边界。不能只提高MaxStartups或关闭校验来掩盖问题。
4. 修复需另开受控变更，验证collector恢复、预检撤销行为、权限与原四实例；不延长证明TTL、不保留失败旧证明、不自动清UNKNOWN。
5. 之后使用新run ID重新安排完整8小时；保留本轮失败，不续跑凑满时长。未经新安排本轮不自动重启。通过前暂不启动E5。

## 原始证据

[最终状态](endurance-final.json)、[SQL验收](endurance-verification.json)、[任务级采样](endurance-requests.jsonl)、[失败任务及隔离状态](endurance-failed-task.json)、[collector日志](endurance-collector-failure.log)、[SSH限流日志](endurance-ssh-throttling.log)、[恢复后模型核对](post-failure-verification.json)、[恢复后监控](post-failure-monitor.json)。

## 2026-10-22 02:58 复发更新

只读巡检发现collector再次COLLECTION_UNAVAILABLE，独立监控为COLLECTOR_FAILED_OR_STALE、UNKNOWN仍为0；原夜间负载保持inactive，没有新任务。002在02:51起再次反复出现MaxStartups限流，现有OCR/worker容器仍持续运行。首次通过公网2023新建SSH连接被reset，但既有001复用会话可读状态。说明采集链路与共享SSH接入的稳定性仍需修复，不能认为首次自动恢复即已解决。

截至02:58快照故障仍存在，不宣布恢复，不修改SSH/网络或预检规则。新推理可能被缺失预检证明拒绝。P4未通过，不自动重新启动负载，不进入E5。

证据：[collector复发日志](collector-recurrence-20261022.log)、[002限流复发日志](ssh-throttling-recurrence-20261022.log)、[监控快照](monitor-recurrence-20261022.json)。
