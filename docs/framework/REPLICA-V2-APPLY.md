# R3a：周期对账的串行写入与接管控制

2026-11-12 · 本地实现，未部署 · [Issue #39](https://github.com/WTaoGitHub/cybercafe-maas/issues/39)

新版 MaaS 执行器可以在没有新 GPU 到达事件的情况下，根据 R2 完整稳定快照执行受授权扩容。v2 的 disabled 模式不要求压力 feed；required 保持冻结；advisory 支持持久化缺样宽限、保守冷却和显式首副本快路径。schema v1 继续使用原 writer，不把周期动作伪装成旧事件。

**本阶段为 serial_pilot，实际每次 +1、全局/集群在途最多 1。** 状态展示 effective_startup_limit=1；即使策略声明更高上限也不会放开并行。R3b 的在途资源预留和并行 2/4 尚未完成，不能宣称启动效率已验证。当前现网镜像、配置和人工副本均未改变。

## 执行与恢复

执行同时要求 v2 policy.mode=apply、运行时写授权、正确版本、受管范围、显式接管、完整稳定快照、当前容量/配置一致、压力模式与启动预算通过。没有 ownership.json 时返回 adoption_required，不自动接管人工维持的模型。

规划、原生快照和发送前均校验，仍使用原生更新 API 并省略只读 ready_replicas。接管/暂停与发送边界由私有进程锁协调：尚未发送的失效动作清理自己的预留日志；可能已发送的超时保留 PendingWrite。暂停不能撤销已经发送的请求。

确认顺序为：原生读回目标 → GET 当前模型复核 → 幂等持久确认回执、接管目标和冷却时间 → 清理 PendingWrite。确认落盘失败时返回 confirmation_unresolved，保留日志，不重发。Observe/暂停后仍可只读确认原动作；读回旧值不是安全重试依据。

advisory 缺样计时与冷却保存于私有状态，进程重建不重置。有效高压保持等待；样本缺失不再混作“低压力”。只有显式首副本快路径可跳过缺样宽限，仍受资源、接管及单个启动名额限制。disabled 与该快路径均不绕过显存/授权/未知写入限制。

## 私有管理命令

以下是新镜像的后续操作示意，**本轮没有在现网执行**。命令在带原生 management key 和受限环境的 E17 容器内以其运行 UID 执行，不新建网络入口。`scope_config_checked` 表示本地 scope 配置已检查，不等于已测试所有原生写权限。

```sh
python -m maas_platform.replica_control status \
  --policy /run/maas-replica/policy.json

# 明确核对实际目标后接管；1=1 只是单模型示例，不得替代现场核对。
python -m maas_platform.replica_control adopt \
  --policy /run/maas-replica/policy.json --expected-target 1=1

python -m maas_platform.replica_control pause \
  --policy /run/maas-replica/policy.json
```

adopt 先读取原生版本及当前模型，要求完整受管范围和显式 expected-target 匹配，检查没有未确认日志，再保存接管基线。它不发送模型 PUT。已有 PendingWrite 时禁止重新 adopt，以免吞掉未知写入。pause 只设置控制代次和暂停状态；发送临界区忙时命令失败，不宣称暂停已完成，需只读核对后重试。恢复接管仍用显式 adopt，无隐式 resume。

当前串行试点中，受管范围内任一部署人工漂移会保守冻结整个 scope；后续逐部署推进需与 R3b 的预留和隔离一起验证，不能当成已完成的逐模型隔离。

## 后续受控部署顺序

1. 先保存私有备份、核对旧 v1 未确认日志为空；未解决时保持旧版只读对账，不跨版本强行接管。
2. 用锁定代码的新 MaaS 镜像，原 Observe + Apply 覆盖后追加 [compose.replica-v2-apply.yaml](../../deploy/maas/compose.replica-v2-apply.yaml)，启用 e17-daily profile；不要叠加会关闭 Apply 的 v2-observe 覆盖。保留私有父控制卷，state/ 由 UID 10001 持有、权限 700。
3. 新版 policy 初始保持 Observe，核对当前预览、实际模型、授权上限及无待确认日志。
4. 明确采用当前副本，核验 adopt 输出；然后以递增 revision 将 policy 设为 Apply。这将允许模型向当前策略可行目标增长，未重新授权前不对现有人工 1/1 执行此步骤。
5. 先独立试点验证 0→1、模式差异、未知结果隔离和 OCR/Higress/异步计量，再决定现网发布。单元测试的模拟成功不代替真实 GPU 验收。

当前状态文件写权限不正确、损坏或 scope 不匹配均停写。实际写入回调只在明确的本地管理授权开启时构造；配置错误阻塞调节器，不以重启 VM 或清除日志处理。

## 测试范围

本地替身覆盖无到达事件 0→1、显式接管/人工漂移/暂停、写前策略变更、未确认写入禁止再次 adopt、压力严格模式/缺样宽限/冷却/首副本快路径、正常高压、快照过期、版本/上限授权、原生填充字段、超时后旧目标与已提交结果恢复、回执落盘失败，以及主循环路由新版执行器。现有 pinned-runtime 条件跳过仍须在 R4 补齐；没有远端模型写入或压力参数实测。

R3a 新增 20 项测试通过；与 MaaS 调节器及共享插件合并 pytest 回归为 90 通过、2 条固定 GPUStack runtime 条件跳过。Compose 覆盖仅做静态验证，尚未构建/重建实际试点服务。
