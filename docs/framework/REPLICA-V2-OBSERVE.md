# R2：持续对账 Observe 与持久接管基础

2026-11-12 · 本地实现，未部署 · [Issue #39](https://github.com/WTaoGitHub/cybercafe-maas/issues/39)

后续 R3a 已在独立受授权 writer 中接入串行写入、持久确认回调和私有接管命令，尚未部署。以下“尚未提供 writer/命令”的描述为 R2 时边界，最新见 [R3a](REPLICA-V2-APPLY.md)。只读覆盖配置依然关闭 Apply。

MaaS 的 E17/调节器入口已能读取新版契约并运行 v2 只读对账：事件或周期唤醒后重新完整读取 worker、部署和实例，使用当前容量计算基础策略目标，不要求有新 GPU 上线事件，也不受旧事件 600 秒有效期限制。schema v1 继续走原先的到达事件和 writer。

**本阶段仅输出预览，不会更新原生模型。** 即使 v2 的 policy 写了 Apply、运行环境保留旧 writer 授权，v2 也返回 `v2_apply_not_enabled`，`writes_enabled=false`；不把周期请求伪装成旧的上线事件。不把本阶段能力写成自动 0→1 已完成。

## 对账行为

- 使用现有完整分页读取函数和同一 GPUStack 客户端，三类读取任一失败会取消其他未完成读取并清空稳定基线，不沿用旧缓存。
- 仅订阅不可用但管理 API 完整可读时，可运行轮询降级预览；schema v1 的订阅条件保持不变。
- 启动和 API 恢复后建立新的稳定基线，比较 GPU 归属、显存占用、受管配置/目标和受管实例状态；忽略心跳时间，忽略其他部署实例的无关生命周期变化。
- 周期目标来自基础策略；不套用旧到达事件的覆盖参数。当前 planner 的未就绪串行保护仍在，并行和在途预留改造留到 R3。
- 压力诊断只针对当前可调度节点集合，能显示旧样本仍覆盖 worker 3，而新 worker 4、5 缺样；disabled 不读取压力源。
- 输出 `state`、`observed_at`、`snapshot_complete`、`snapshot_age_s`、`stable_for_s`、候选目标、压力原因、接管与 PendingWrite 状态。`executable_preview_limit=0` 明确表示本阶段没有可执行额度。日志只在状态变化时输出，不因样本年龄或心跳时间每秒刷屏。

## 私有状态

`ControlState` 为后续真实写入提供独立的接管基础，使用现有控制卷下的 `state/`，不新建数据库或网络服务：

- `ownership.json`：明确采用的集群/所有者、完整部署范围、配置指纹、目标、副本写入时钟、控制代次及确认回执。
- `ownership.lock`：私有非阻塞进程锁；冲突时失败而非永久阻塞事件循环。
- `status.json`：最新只读预览。无效目录、写入失败不放开权限，也不使 E17 日报循环退出。

文件 600、目录 700，拒绝符号链接、不安全权限和无效状态。接管必须显式核对全部部署及期望目标，不能自动采用旧副本数。发现人工改目标或配置、暂停、范围改变均可识别；确认函数幂等保存新目标与时钟，重复确认不重置冷却。

R2 已测试这些持久状态函数，但**尚未提供可操作现网的接管 CLI，也未接入实际 writer 的确认回调**；这两项在 R3 实现。Observe 可显示 adoption_required，不需要为了看预览就先取得写入权。没有配置持久目录时只输出内存预览，不宣称持久化已经生效。

## 后续部署配置示意

新增 [compose.replica-v2-observe.yaml](../../deploy/maas/compose.replica-v2-observe.yaml) 可与基础 Compose、原 Observe 覆盖一起使用，要求包含 R2 代码的 MaaS 镜像、新版 Observe policy 和独立的 `MAAS_REPLICA_CONTROL_STATE_DIR`。

目录应先按 UID/GID 10001:10001、700 创建；该覆盖显式设 `MAAS_REPLICA_ENABLE_APPLY=0`。不要直接套到旧镜像，也不要把它当作 Apply 发布覆盖。本轮未执行 Compose 重建或部署。

## 测试与剩余工作

专项用完整 API 替身验证：已有 Ready GPU 无到达事件仍算出目标、超过 600 秒仍可重算、API 故障清空基线与取消其他读取、心跳不重置稳定期、旧节点样本诊断、冲突身份冻结、Pending 等待、主循环绝不调用旧 writer，以及显式接管、人工漂移、私有权限、持久确认和重复确认冷却。

R2 新增 12 项测试通过。MaaS 调节器及共享插件合并 pytest 回归为 70 通过、2 条固定 GPUStack runtime 条件跳过；不把跳过项当原生集成通过。另完成本地文档链接、私有状态格式及 Compose 覆盖静态检查，未连接或修改服务器。

R3 再接入受授权写入、最终校验、原生读回确认、接管管理命令、advisory 与启动预算；R4 才使用独立试点做真实 GPU、Higress 和启动效率验收。当前现网配置和人工副本保持不变。
