# R1：新版调节策略契约与只读诊断

> **2026-11-12 最新职责边界（候选已实现，未部署）**：调节器只管理副本数量；外部镜像、启动参数和资源声明变化不回滚、不暂停、不要求重新接管。每轮读取 GPUStack 当前显存声明规划新副本，旧实例仍按原生实际占用核算；v2 策略中的重复显存字段改为可选且不作规划依据。授权范围变化和未知写入保护保留，全局暂停优先，按模型控制仍是后续增强。此前“普通配置变化冻结”的记录已被本决议替代。本轮未连接或修改服务器。[职责与资源口径](REPLICA-RESOURCE-INPUTS.md) · [本地验收](../../results/maas-replica-resources-20261112/REPORT.md) · [Issue #39](https://github.com/WTaoGitHub/cybercafe-maas/issues/39)。

> **2026-11-12 前一版控制记录（普通配置冻结规则已被后续决议替代）**：新增运维全局暂停/恢复/查询 API。未暂停的 v2 Apply 根据当前策略和 GPU 容量纠正外部副本数量修改，支持受服务下限保护的逐个缩容；数量变化不再触发旧的人工漂移冻结。镜像等保护配置和未知写入仍阻止动作。接管不隐式解除暂停，恢复须显式 resume；新增模型事件唤醒。141 项调节/E17/插件回归与 18 项客户接口回归通过，3 项环境条件跳过。旧 R4 人工归零停写结果仅作历史证据；新语义尚待独立真实试点和 R5 发布。本轮未连接或修改服务器。[操作与设计](REPLICA-CONTROL-API.md) · [本地验收](../../results/maas-replica-control-20261112/REPORT.md) · [Issue #39](https://github.com/WTaoGitHub/cybercafe-maas/issues/39)。

> **2026-11-12 R1—R3 代码和 R4 只读前检完成，未发布**：102 项本地回归通过（Mac 缺原生 SDK 的 2 项另在固定 runtime 补验），GPUStack v2.2.3 内 34 项回归全部通过。真实两卡 Ready、原 OCR 仍 1/1；新版 Observe 稳定后计算目标 2，因未接管而保持零写入。已修复原生空 distributed_servers 默认对象导致误冻结的问题。独立实写/启动耗时比较、故障恢复试点及 R5 发布未完成，现网配置与人工目标不变。[实施与证据](../../results/maas-replica-v2-20261112/REPORT.md) · [并行边界](REPLICA-V2-PARALLEL.md) · [Issue #39](https://github.com/WTaoGitHub/cybercafe-maas/issues/39)。

2026-11-12 · 本地实现，未部署 · [Issue #39](https://github.com/WTaoGitHub/cybercafe-maas/issues/39)

最新进展：R3a 已接入独立的 v2 串行 writer 和私有接管命令，尚未部署；历史阶段边界不代表最终代码仍拒绝所有 v2 写入。[R3a 状态](REPLICA-V2-APPLY.md)。

后续 R2 已将 v2 Observe 接入 MaaS 主循环，支持当前状态周期预览；实际 v2 写入仍明确禁用。以下“入口尚未接入”的描述是 R1 交付时边界，最新见 [R2 实现](REPLICA-V2-OBSERVE.md)。

R1 增加 MaaS 专用 schema v2 解析、私有策略热加载、只读压力诊断命令，并为既有 PressureGate 补充结构化原因。原有 gate 的 open / pressure_unavailable / pressure_high / pressure_recovering 返回语义保持，旧插件仍仅接受 schema v1。

**本阶段没有启用周期对账或放开压力门槛。** E17 正常入口仍使用旧 loader，会拒绝 schema v2。新版契约用于本地开发、校验和后续 R2/R3；不能把示例直接替换现网 policy。

## 契约

示例见 [replica-policy-v2.example.json](../../deploy/maas/replica-policy-v2.example.json)，默认 Observe。集群、组织、部署、显存和上限必须按目标环境核对，不代表已有写入授权。

- `reconcile.interval_s`：1—300 秒；`stability_s`：0—120 秒。
- `startup.degraded_grace_s`：0—300 秒；`degraded_write_interval_s`：1—3600 秒。
- `startup.degraded_inflight_max`：1—32 且不得超过全局/集群正常在途上限。
- `startup.first_replica_fastpath`：显式布尔值，默认 false；required 模式禁止开启。
- `pressure.mode`：required / advisory / disabled，必须显式配置。前两种需要原有水位及样本时效字段；disabled 不允许携带会被忽略的阈值。
- `execution` 显式包含 mode、scale_up_batch_max 及两个在途上限，新增批次/在途上限范围为 1—32。只定义预算，不代表该阶段已支持并行执行。
- 热加载要求私有普通文件、拒绝符号链接；内容变化必须递增 revision。Observe 解析器不能接受 Apply，即使内容语法正确。

旧 schema v1 保持原 Policy 行为，pressure_mode 记录为 required；未配置压力的旧 Observe 仍可读取，但不因此取得写权限。新契约不影响 GPUStack 内旧插件的策略文件。

## 本地只读诊断

先将示例复制为本地私有文件，权限设为 600；不要修改仓库示例文件权限作为运行状态。

```sh
PYTHONPATH=.:plugins/gpustack-replica .venv-maas/bin/python \
  -m maas_platform.replica_status \
  --policy /absolute/private/policy.json \
  --pressure-file /absolute/private/pressure.json \
  --worker-id 4 --worker-id 5
```

worker ID 来自管理员当前核对的参与规划节点；本命令不连接 GPUStack、不自动发现节点、不发起扩容。`read_only=true`、`runtime_authorization_checked=false` 明确表示它不是执行授权或整体运行健康证明。

事故复现的关键输出如下（样本时间和 ID 为测试数据）：

```json
{
  "state": "pressure_unavailable",
  "reasons": ["sample_stale", "missing_workers"],
  "sample_age_s": 100,
  "required_worker_ids": [4, 5],
  "covered_worker_ids": [3],
  "missing_worker_ids": [4, 5]
}
```

另可区分 sample_missing、sample_unreadable、sample_invalid、未来时间/时钟无效、样本乱序、高水位、低水位恢复等待等原因。不输出原始采样内容、SSH 地址或凭据。单次诊断新建 gate，因此不能证明连续低压恢复窗口已完成。

## 验证与剩余工作

测试覆盖 v1 行为兼容、v2 三种模式、显式 Apply 授权、字段/类型/范围拒绝、事件覆盖权限、递增热加载、私有权限/符号链接，以及旧 worker 样本与新 ID 不匹配的事故复现。R2/R3 再把契约接入持续对账、持久接管和真实执行路径；R4 才验证原生写入与启动效率。当前现网与人工副本不变。

验证：R1 新增 11 项测试通过；MaaS 调节器专项 20 项中 19 通过、1 项因缺少本地固定 GPUStack runtime 条件跳过；共享插件回归 24 项中 23 通过、1 项固定 runtime 回放模块条件跳过。两类跳过不计为已验证的原生集成，未运行实际 GPU 或远端测试。

## 显式暂停与自动数量纠正补充（2026-11-12）

v2 执行契约增加可选 `scale_down_batch_max`，范围 0—32，默认 1；0 禁止自动缩容。当前实际写入仍每次 ±1，不因放宽批量数字一次删除多个副本。全局暂停由持久控制卷保存，通过独立运维 API 或 CLI 设置；不修改策略 revision，不因 adopt 而隐式恢复。计数和普通配置变化更新观察基线；配置保留最新值，只改副本数。授权范围变化和未知写入仍阻止动作。详见[控制说明](REPLICA-CONTROL-API.md)。旧 v1/插件入口不变。

## 当前原生资源声明优先（同日后续决议）

v2 `models.<id>.vram_claim_bytes` 已改为可选兼容字段，不再决定容量；每轮从 GPUStack 当前模型的 `env.GPUSTACK_MODEL_VRAM_CLAIM` 读取字节字符串。缺失或非法不回退旧策略值、不改变显式暂停状态，修复后重新规划。实例原生实际占用与未来副本声明分别核算，资源规格变化不会被当作已经释放旧资源。详见[当前资源输入方案](REPLICA-RESOURCE-INPUTS.md)。schema v1/插件资源核对规则保持。
