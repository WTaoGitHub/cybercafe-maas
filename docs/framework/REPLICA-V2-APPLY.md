# R3a：周期对账的串行写入与接管控制

> **2026-11-12 R4 实际 E17 主循环演练通过，原配置已恢复**：实际订阅/写库与 v2 Observe、显式接管、自动 0→1/1→2、暂停跨进程恢复、人工漂移停写、错误接管拒绝均通过。修复 E17 SIGTERM 退出（e447154），候选实际退出码 0；2 次原生 OCR 与 1 个唯一计量的 MaaS 异步任务成功。原 OCR 1/1、试点 0/0、UNKNOWN=0，恢复原 E17 镜像/环境和 v1/revision 7 策略，未正式启用 v2。完整 R4 仍缺隔离通道/在途不确定写入及冷缓存竞争；R5 未发布。[报告](../../results/maas-replica-v2-e17-20261112/REPORT.md) · [HTML](../../results/maas-replica-v2-e17-20261112/REPORT.html) · [Issue #39](https://github.com/WTaoGitHub/cybercafe-maas/issues/39)。

> **2026-11-12 R4 首轮真实试点通过，常驻未切换**：12 个实验、27 次自动 +1 确认、6 个容量点、7 次原生 OCR 与 2 个唯一计量的 MaaS 异步任务均通过。两卡达到 4 副本全部 Running，串行两轮均值 37.85 秒、上限 2 为 21.61 秒（缩短约 42.9%）；上限 4 实际在途仍峰值 2。原业务保持 1/1，原 E17 及 v1/revision 7 策略不变，试点归零、无待确认写入、UNKNOWN=0，临时 key 已撤销。完整 R4 尚缺常驻事件循环、受控冷热缓存及实际故障恢复；R5 未发布。[报告](../../results/maas-replica-v2-r4-20261112/REPORT.md) · [HTML](../../results/maas-replica-v2-r4-20261112/REPORT.html) · [Issue #39](https://github.com/WTaoGitHub/cybercafe-maas/issues/39)。

> **2026-11-12 R1—R3 代码和 R4 只读前检完成，未发布**：102 项本地回归通过（Mac 缺原生 SDK 的 2 项另在固定 runtime 补验），GPUStack v2.2.3 内 34 项回归全部通过。真实两卡 Ready、原 OCR 仍 1/1；新版 Observe 稳定后计算目标 2，因未接管而保持零写入。已修复原生空 distributed_servers 默认对象导致误冻结的问题。独立实写/启动耗时比较、故障恢复试点及 R5 发布未完成，现网配置与人工目标不变。[实施与证据](../../results/maas-replica-v2-20261112/REPORT.md) · [并行边界](REPLICA-V2-PARALLEL.md) · [Issue #39](https://github.com/WTaoGitHub/cybercafe-maas/issues/39)。

2026-11-12 · 本地实现，未部署 · [Issue #39](https://github.com/WTaoGitHub/cybercafe-maas/issues/39)

新版 MaaS 执行器可以在没有新 GPU 到达事件的情况下，根据 R2 完整稳定快照执行受授权扩容。v2 的 disabled 模式不要求压力 feed；required 保持冻结；advisory 支持持久化缺样宽限、保守冷却和显式首副本快路径。schema v1 继续使用原 writer，不把周期动作伪装成旧事件。

**本节描述默认 serial_pilot 路径，实际每次 +1、全局/集群在途最多 1。** 未开启 startup.parallel_startup 时，提高策略上限不会自动放开并行。显式并行选项见 R3b 文档；实际启动效率仍待 R4。当前现网镜像、配置和人工副本均未改变。

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

当前串行试点中，受管范围内任一部署人工漂移会保守冻结整个 scope；这与模型启动慢时允许其他模型继续启动不同，尚不是逐部署人工接管隔离。

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

## E17 正常停止与恢复

e447154 起，`python -m maas_platform.gpu_daily_watch` 的服务入口接收 SIGTERM，取消并等待既有采集/订阅/调节任务清理；重复 SIGTERM 不再次取消清理。异常及非停止来源的取消继续传播。无需增加端口、依赖或数据库结构。

运维先暂停调节器并核清 PendingWrite，保留私有控制卷后普通停止 E17 容器，沿用 Compose 的停止宽限。退出码 0 只确认进程完成退出清理，原生模型写入是否成功仍看持久确认日志；不据退出码清除未确认请求。停止 E17 不自动缩容模型。恢复时使用原持久卷和授权范围；观察订阅与新鲜写库，再显式采用当前目标解除暂停。E17 日报采集继续保持单实例数据库锁，不并行启动两个采集进程。

实际演练和回退已通过，见[E17 报告](../../results/maas-replica-v2-e17-20261112/REPORT.md)。当前原常驻镜像已恢复，尚不含该修复，后续随 R5 发布。真实写入在途时的网络不确定结果仍须独立验证，不因本次暂停状态重启成功而标记完成。
