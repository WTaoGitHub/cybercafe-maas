# 首次新卡从开机到 Ready 的自动上线验收

2026-11-13，用户补充确认：管理员先停止原 003，再重新应用新版快照启动；并非保持原 003 在线后另外新增机器。002 未先关闭。本轮 Agent 仅通过 SSH、启动日志、MaaS 授权状态和原生 worker 表只读取证，没有安装、改配置、手工预登记、交付许可或重试服务，不执行任何平台或 VM 操作。

## 验收结果

新环境名称为 XZ-31-003，17/17 安装文件匹配 `b6ab211`，使用与原 worker 4、5 不同的 GPU。启动时间线使用开机后的单调秒数：

| 事件 | 秒 |
| --- | ---: |
| 供应商命名完成 | 12.097045 |
| timer 到期 | 30.081084 |
| 系统基本目标就绪 | 104.263915 |
| E5 主进程启动 | 104.288092 |
| APPLIED / Ready | 120.936123 |
| Agent 首次读取时的运行时间 | 206.11 |

日志只有一次 E5 启动，第一次尝试即成功，完成时间早于本轮 Agent 首次连接。原生 `me=true` 经受信 HTTPS 返回 200：集群 1、worker_id=6、Ready、unreachable=false，name、external_id、实测 GPU UUID 与上报清单一致。

MaaS 对应本次 boot ID 的 intent 为 automatic，初始 worker_id 为空，随后 issued 的 worker ID 为 6；该 GPU 不在逐卡批准映射中。原生 worker 的创建时间位于 intent 写入与许可持久化之间，支持本次由自动流程完成新记录预登记，而非事先人工建档。比较使用服务端 UTC 时间与文件 epoch，未将无时区数据库值按本地时区解释。

**换快照后的冷启动、首次新卡从开机到 Ready、无 Agent 手工触发或注册前置操作的验收通过。** 002 是否先关闭不是这个场景的前置条件；本批没有覆盖同卡接续。平台实例 ID 未取得，不能判断平台是否在重建时更换了实例 ID。

## 原有节点状态与操作计划差异

- 原 003 对应 worker 4 已为 NotReady，原 SSH 入口连接超时，与用户随后确认的原 003 已停机并重新应用快照相符。操作依据为用户确认，未额外查询供应商平台状态。
- 原 004 对应 worker 5 仍 Ready，SSH 回读的 boot ID 与原值一致。
- 新环境也叫 003，但 GPU 不同，注册为独立 worker 6；没有复用或覆盖 worker 4 的 GPU 身份。后续应按 GPU UUID、worker ID 和平台实例 ID 区分机器。

本轮实际操作与“保持 003、004 不变、替换 002”的原计划不同，但仍覆盖冷启动首次新卡自动上线。由于新旧 GPU UUID 不同，worker 4 到 worker 6 的变化符合身份规则；同卡接续仍需另行验证。

[结构化证据](verification.json)仅保存设备与启动 ID 的摘要，不含连接信息、凭据或原始私有日志。关联 [身份验收 #16](https://github.com/WTaoGitHub/cybercafe-maas/issues/16)、[启动时序 #20](https://github.com/WTaoGitHub/cybercafe-maas/issues/20)。
