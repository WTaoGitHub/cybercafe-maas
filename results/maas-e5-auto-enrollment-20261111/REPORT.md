# 全新 GPU 自动预登记与注册修复

2026-11-11，修复 [Issue #16](https://github.com/WTaoGitHub/cybercafe-maas/issues/16) 中自动入口提前要求 GPU 批准映射、导致全新 GPU 无法调用已有预登记逻辑的问题。代码 [1a6dbad](https://github.com/WTaoGitHub/cybercafe-maas/commit/1a6dbadbb14130e0814c9625ecd4dbaffbe1d36d) 已推送并部署到新 PC 的无盘 Bootstrap。

## 行为变化

在受认证的批次授权项中启用 `auto_enroll_gpus: true` 后，合法集群、镜像与发布范围内的新 GPU 无需逐卡预建 worker 或填写批准映射。MaaS 在原有集群/GPU/启动互斥下调用已有预登记接口，事务创建或匹配 `name=external_id=GPU UUID` 的记录，再签发许可，E5 自动写入临时身份并启动原生 worker。

该开关缺省关闭，旧的批准名单模式继续可用；显式 `auto_start_gpus` 绑定仍约束原 worker ID，非法策略类型拒绝发证。原生插件没有改动，唯一绑定、冲突拒绝、单次许可及同卡仅 `not_ready` 可接续的规则继续生效。

## 验证

Bootstrap 全量测试 **164 项通过**，其中新增 23 个回归场景。首次新卡正向用例从没有人工许可、GPU 批准映射或原生记录开始，覆盖新卡预登记与发证、完整 E5 调用链、重复请求、Broker 重建后复用许可、同卡并发争用、多 GPU 独立绑定、响应丢失和模糊签发结果。无认证、错误集群/镜像/发布、无效 UUID/启动 ID、绑定不匹配及非法策略类型均拒绝；保留受限模式和 Ready 拒绝/NotReady 接续的回归。

生产验证使用先前超时的两个独立 VPC。修改前只读查询确认其 GPU 在原生 worker 表中均无记录；没有手工预登记或向批准映射添加 GPU。服务端修复后，分别重触发现有引导服务，由程序完成整个链路：

| 节点 | 原生 worker ID | 集群 | 结果 | 本次引导耗时 |
| --- | --- | --- | --- | --- |
| XZ-31-003 | 4 | 1 | 首次尝试 APPLIED，Ready | 16.247175 秒 |
| XZ-31-004 | 5 | 1 | 首次尝试 APPLIED，Ready | 16.285324 秒 |

两台使用各自 worker 凭据经受信 HTTPS 查询 `me=true` 均返回 200，`name`、`external_id`、实际设备 UUID 与上报清单一致，`unreachable=false`。原生表各只有一条有效绑定，handoff 均为 active。MaaS intent 的 `worker_id` 起初为空，自动取得 ID 后记录 issued，证明经过新卡预登记分支。

## 部署范围与回退材料

- 仅重建新 PC 的无盘 Bootstrap 容器；原镜像、UID、环境、host 网络和端口保持原值，14 个源码文件摘要与提交一致。普通配置服务和原生 enrollment 插件未更新。
- 当前集群 1 的受认证批次已开启自动预登记。原有两个 GPU 批准映射保持原值，新卡没有人工映射条目；策略文件 UID/GID 与权限保留。
- Bootstrap 健康接口返回 200，未认证自动申请返回 401。旧容器停止后保留，旧配置、授权状态及容器规格已保存到新 PC 的 root 私有备份目录。源版本和当前容器信息已持久记录。
- 客户端仍完整匹配 `b6ab211`，17/17 文件未变，本次无需更新母版快照。002 仅用于访问跳板，没有升级或启动它的 worker。

本次没有 VM/操作系统重启，没有手工发放启动许可，也未检查或调整模型服务。原始连接信息、凭据、GPU UUID、boot ID 和私有日志不公开；公开证据仅保留设备及会话摘要。见[核验结果](verification.json)与[部署源码摘要](source-manifest.json)。

## 验收边界

本轮实测证明全新 GPU 从无记录、无逐卡批准映射到自动预登记、发证和 Ready 的链路。两台 VPC 的旧任务已经超时，本轮使用了服务级重试，不能把旧的首启失败改判为零干预成功。下一次由管理员启动使用现有新版快照的独立 VPC，再确认从开机到 Ready 全程无需人工触发；本轮未重新进行整机同卡接续实验。此前真实首启的延后触发证据见 [启动取证](../maas-e5-late-startup-20261111/LIVE-CLONES.md)。
