# P3 GPUStack维护与节点进程停止

本文记录P3阶段验收。2026-10-21已完成常驻服务正式维护绑定与演练，当前操作请使用 [P4正式维护手册](P4-MAINTENANCE.md)。

2026-10-20 · [实机报告](../../results/maas-p3-node/REPORT.html)。

## 范围与当前状态

W6的受控进程级维护闭环已在现有两节点通过：001运行控制端，002为维护目标；目标集群两个模型的副本全局降为0，001的GPU推理也暂停，但控制端和SSH一直在线。未关闭VM、重启宿主机或切换Windows/Ubuntu，`safe_to_poweroff`仍为false。

任务API/执行器依旧是临时验收部署，未进行P4常驻发布。操作器源码、002固定命令代理和专用SSH通道已准备；本轮临时任务配置、数据库及API已清理，不能直接复用已删除的验收配置。

## 实现

- `maas_platform/node_maintenance.py`：维护状态机、私密恢复日志、GPUStack v2.2.3版本检查、模型范围/配置指纹与维护generation检查。
- `scripts/maas/runtime/maintenance_control.py`：001私有运维CLI；以文件锁串行化同一会话，不新增公网管理接口。
- `scripts/maas/runtime/maintenance_node002.py`：固定002与原worker容器身份，仅支持arm/stop/restore/disarm/status，不执行VM关机，不创建worker或Docker daemon。

停止步骤：

1. 显式枚举目标集群全部模型，保存原副本数、节点分布、配置指纹及002的name/labels/maintenance。
2. 暂停任务/受控同步入口，等待受控在途结束；有UNKNOWN立即拒绝后续部署变更，不自动释放。
3. 设置002 maintenance；它只改变节点状态与调度资格，不认为模型已经停止。
4. 全局副本降为0，等待GPUStack实例记录及实际推理容器清理；不直接强删模型Docker容器。
5. 暂停002旧cybercafe-agent，避免它在维护窗口执行旧部署命令；停止原worker并关闭其Docker自动重启。
6. 只读确认：worker已停、GPU工作负载容器为空、NVIDIA计算进程为空。保留SSH管理与VM运行。

恢复步骤：

1. 恢复原worker容器的restart policy并启动，同一个容器ID；恢复原Agent运行状态。
2. 还原worker maintenance（包括原值null）及原副本数；name/labels不变。
3. 等实例running并核对原节点分布。`spread`不保证并发恢复时严格分散：必要时通过GPUStack实例API重建过量节点上的实例，最多使用两倍原副本总数的纠偏预算；不改selector或固定版本，不无限重试。
4. 等常驻collector恢复，执行真实OCR恢复探针；维护generation变化、配置漂移或探针失败均保持派发暂停。
5. 只有验证通过才resume并撤销恢复定时器。

## 恢复日志和故障处理

每个会话使用全新的journal文件（mode600），已有日志不能覆盖。日志记录原值、阶段、配置SHA和副本纠偏，不记录管理key。阶段包括PAUSED、ARMED、MAINTENANCE_ENABLED、SCALING_DOWN、REPLICAS_ZERO、STOPPED、RECOVERING、RECOVERED、RESUMED。

操作器持锁，受控任务数据库的maintenance generation另行阻止新维护窗口被旧探针打开。GPUStack没有跨系统事务，观察到模型集合、配置、版本、worker maintenance所有者或generation不一致时拒绝继续，留给运维检查，不强行覆盖其他变更。

002有600秒独立恢复定时器，只恢复原worker/Agent，不恢复任务派发；001演练还设置480秒best-effort配置恢复定时器。CLI命令与网络调用有超时；这些机制不等于控制端高可用，也不能替代VM关机后的带外开机能力。恢复不完整时验收脚本保留私密配置/API/数据库供诊断，而不是先清理恢复所需材料。

维护已成功完成的会话不再用于新窗口。`recover`用于恢复部署，**不负责resume任务**；真实探针是单独的resume步骤。UNKNOWN仍须实际终止证据，操作器不自动解除。

## 运维接入

001专用私钥位于`/root/wtaobj/maas-maintenance-access/id_ed25519`，不挂载进任务API/执行器；known_hosts复用已固定的可信主机公钥。

002 authorized_keys限定来源10.11.31.1，使用restrict和固定command。只接受上述五个精确动词，其他命令、SFTP、PTY与转发不开放；实测任意`id`命令被拒绝。常规SSH运维密钥保留，便于失败恢复。

操作命令形态（需先配置有效的常驻或受控验收环境）：

```sh
python3 scripts/maas/runtime/maintenance_control.py --config /private/operator.json begin
python3 scripts/maas/runtime/maintenance_control.py --config /private/operator.json stop
python3 scripts/maas/runtime/maintenance_control.py --config /private/operator.json status
python3 scripts/maas/runtime/maintenance_control.py --config /private/operator.json recover
python3 scripts/maas/runtime/maintenance_control.py --config /private/operator.json resume
```

mode600配置字段：journal（每窗口唯一）、worker_id（当前仅2）、model_ids（须覆盖整个目标集群）、task_cli（带私密环境的任务CLI参数数组）、credential_id、probe_image（容器内只读图片路径）、node_ssh（专用固定命令SSH参数数组）、wait_seconds。JSON中不写明文key，只引用私有文件。

复现本轮独立验收：`scripts/maas/runtime/test_maintenance.py --edge --node-maintenance`，只允许001；要求独立测试PostgreSQL、既有模型1/9、已部署私有edge与专用SSH通道。该脚本会中断集群GPU推理，不是普通健康检查。

## 剩余边界

- 受控请求先排空；运维SSH/可信节点的直连不计入任务台账，维护窗口不得并发发起直连推理。副本归零可能中断这类未纳管请求，不承诺逐节点无损drain。
- H3和旧隧道保持停用；002旧Agent仅在窗口内停止，退出后恢复。拥有root/管理权限的并发外部操作不属于可自动排除的威胁。
- 暂不支持任意节点、多个集群并行维护或操作001控制端停机。
- 网络策略启动持久性、Docker重启窗口、公开HTTPS、常驻部署、备份与8小时耐久属于P4；真实VM断电/双系统切换尚未验证。
- W7已验收；常驻环境已补正式维护配置，当前操作以 [P4维护手册](P4-MAINTENANCE.md)为准，本文保留P3验收过程。
