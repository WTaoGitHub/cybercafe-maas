# 夜间验收失败原因分析（2026-10-22补充）

结论：**已确认直接原因是collector撤销预检证明，任务在推理发送前失败；高概率诱因是公网连接压力影响共享SSH服务的未认证连接容量。SSH是最强线索，尚不能作为每轮采集失败的唯一已证明根因。** 本轮只读分析，没有修复配置或重启负载。

## 1. 已确认的直接调用链

1. 001 collector每轮先执行本地Docker清单，再通过SSH访问002执行固定inventory命令，随后读取GPUStack控制面与网关策略。
2. inventory_command指定002内网地址10.11.31.2，不显式指定端口，使用22；每轮独立调用ssh，无ControlMaster/ControlPersist。连接超时3秒，子进程总超时10秒，正常采样后等待5秒。
3. 任一阶段抛异常时，collect_once删除root和consumer两个preflight.json，写不健康状态，当前仅记录通用COLLECTION_UNAVAILABLE。
4. 任务执行前OCRAdapter.preflight读取consumer证明，文件不可读/不存在等会转换为PREFLIGHT_UNAVAILABLE。
5. 此异常默认不可重试，执行器把任务标记FAILED。preflight发生在begin_backend之前；对失败task_c3700c70e55a46baac527699daf285af进行SQL核对，backend_activity计数为0，说明该受控attempt没有进入已登记发送阶段。
6. 耐久脚本见任务失败即结束，不吞掉失败继续跑；故2360提交/2359成功，未完成8小时。这符合原验收门槛，不是满8小时后统计误差。

## 2. 时间线（北京时间）

| 时间 | 观察 |
|---|---|
| 10月21日22:16:21 | 002 sshd开始MaxStartups限流 |
| 22:16:28 | 001→002内网仍有成功公钥登录，说明不是全时段网络中断 |
| 22:16:34 | collector首次记录COLLECTION_UNAVAILABLE并撤销证明 |
| 22:16:37.911 | 失败任务接纳 |
| 22:16:38.286 | 任务FAILED/PREFLIGHT_UNAVAILABLE，attempt=1，backend intent=0 |
| 22:19:28 | 本次已保存窗口中的最后一条collector失败日志 |
| 22:19:38 | SSH结束持续3分17秒的限流，日志汇总932个连接丢弃 |
| 10月22日02:58 | collector与SSH限流再次同窗复发 |
| 04:00及后续巡检 | collector自动恢复健康，未改配置；不等于根因修复 |

两次同窗复发、collector每轮新握手、外部未认证连接突增及自动恢复共同支持“共享SSH准入容量受影响”的判断。现有证据未显示需要以OCR GPU容量不足解释本次失败；但不据此排除所有尚未记录的系统异常。

## 3. 为什么内网专用key仍可能受影响

当前002的sshd监听所有地址的22端口，collector与公网映射访问的是同一SSH服务。只读读取的当前有效配置为MaxStartups=10:30:100、LoginGraceTime=120、PerSourceMaxStartups=none；这是当前配置快照，不独立证明事发时配置从未变化。

MaxStartups限制同时尚未认证的连接，start:rate:full形式表示达到start后按rate概率拒绝新连接，直到full全部拒绝。[OpenSSH官方说明](https://man.openbsd.org/sshd_config#MaxStartups)。它不是“10个已登录用户”的限制。来源/专用key/forced-command在后续阶段验证，不能替代握手前的连接容量隔离；增加同一sshd的监听端口也不等于隔离其进程级容量。

002事件窗口检索到27次001内网成功认证，说明影响是间歇性的。未找到明确带内网来源的drop日志，因此不能说“已从日志逐条证明collector连接被MaxStartups丢弃”；未保存collector stderr使这点无法事后精确补齐。仅有成功认证也不能证明固定inventory命令后续Docker采集成功。

## 4. 当前暴露的实现薄弱点

- 数据面推理的可信准入依赖共享管理SSH链路，该链路会受到外部未认证连接压力影响。
- 每约5秒新建SSH连接，容易反复遇到握手准入限制，缺少独立稳定通道。
- 采集异常被合并成通用码，无法区分SSH握手/超时、远端inventory退出66、JSON解析、控制面读取或网关探查故障。
- 采集失败立即撤销证明是安全保护，但当前对可证明尚未发送的任务没有有界延后策略，暂态故障会直接成为业务失败。不能据此放宽版本/镜像检查或容忍未知后台执行。
- runner业务FAILED仍退出0，systemd Result=success；必须看status/verification，后续应改进进程退出状态与验收监测的一致性。

## 5. 建议修复方向

先补安全分类诊断并验证SSH因果链，再为只读清单使用与公网SSH连接压力隔离的受限通道，或验证安全持久连接的作用/断线恢复；同一sshd简单换端口不算容量隔离。不改变原host-key/forced-command/来源权限，也不盲目调大MaxStartups。

如要改善暂态业务失败，可另行设计“发送前预检暂不可用时有界等待/延后”，严格保留总deadline/队列/项目额度，不对已发送或UNKNOWN重试。修复后用新run ID完整重测，不能续跑本轮时长。

原始材料：[失败记录](ENDURANCE-FAILURE.md)、[失败任务](endurance-failed-task.json)、[collector日志](endurance-collector-failure.log)、[SSH限流](endurance-ssh-throttling.log)、本仓库maas_platform/collector.py与ocr_connector.py。

## 6. 001同窗SSH故障补充

2026-10-22按用户问题进一步核对，**不能称为仅002故障**。001的ssh.service日志证实：10月21日22:16:20开始MaxStartups限流，22:20:35结束，持续4分15秒，记录2372次连接丢弃；002则在22:16:21—22:19:38限流、丢弃932次。10月22日复发时001在02:51:45开始，002在02:51:48开始，仍为同窗现象。证据见 [001日志摘要](ssh-throttling-001-correlation.json)。

须区分SSH故障范围与collector的依赖方向：001 collector对本机用Docker清单、对002用SSH，因此002的SSH准入是直接远程采集依赖；001的SSH限流直接影响新建运维连接，但collector不经SSH读取001本地清单。不能据此把001的模型/API也判为宕机，或完全排除共有主机资源压力。未记录采集失败阶段的证据缺口仍在，修复应同时审查两节点公网/管理SSH准入，并稳定只读清单通道。
