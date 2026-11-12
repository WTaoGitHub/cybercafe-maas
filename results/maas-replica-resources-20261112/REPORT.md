# 只调节副本数量与原生资源输入验收

2026-11-12 · 本地候选通过，未部署 · [Issue #39](https://github.com/WTaoGitHub/cybercafe-maas/issues/39)

用户最新明确：调节器只允许修改副本数量；外部修改模型镜像、资源声明等配置，不回滚、不暂停，但后续副本扩容使用最新声明。此决议替代此前普通配置漂移冻结的候选规则；全局暂停与后续逐模型控制的分阶段决定保持。

## 实现

- 新增 snapshot-local 资源解析，读取 GPUStack 当前模型显存声明。v2 策略中的重复显存值改为可选且不作规划依据；不要求手工同步两份规格。旧 v1/插件入口保持原资源核对语义。
- 普通配置变化刷新观察基线，不要求再次 adopt。授权范围改变、全局暂停、未知写入保护继续生效。资源缺失/非法给出明确诊断，不回退旧值，不写 paused；修复后自动重算。
- 新副本按新声明，旧实例保留其原生实际占用。预算取 worker 已分配与实例原生声明总量的较大值；混合规格不重新定价旧实例，仍可用已证明的剩余量新增。CPU/RAM 配置保留，由原生调度约束，未新增联合求解。
- 最终发送边界仅允许 replicas 字段与最新快照不同。GPUStack v2.2.3 的 ModelUpdate 仍要求部署字段，因此其他字段按最新值原样回填；不宣称已实现单字段原子 PATCH。写前配置或实例资源变化会使旧计划失效。
- 已发送的不确定请求仍只读确认；配置更新不能清除或重置 PendingWrite。没有因本轮实现修改引擎镜像、环境、资源配置或 worker。

## 本地验证

**160 passed / 3 skipped**，保留一个既有 Starlette/AnyIO 弃用警告。3 条跳过涉及固定 GPUStack SDK/独立数据库条件，不作为真实集成通过。共享插件及 v1 回归包含在本轮命令中。

```sh
PYTHONPATH=.:plugins/gpustack-replica .venv-maas/bin/python -m pytest \
  tests/maas/test_gpu_daily*.py tests/maas/test_replica_controller.py \
  tests/maas/test_replica_apply.py tests/maas/test_replica_observe.py \
  tests/maas/test_replica_contract.py tests/maas/test_replica_reconcile.py \
  tests/maas/test_replica_writer_v2.py tests/maas/test_replica_trial.py \
  tests/maas/test_replica_control_web.py tests/maas/test_replica_targets.py \
  tests/maas/test_replica_resources.py plugins/gpustack-replica -q
```

验证覆盖：镜像配置变化不暂停/不回滚；数量已满足时不发请求；最新声明覆盖旧提示且不修改源对象；声明增大/减小后的规划；暂停期间更新后恢复；旧实例占用不因新声明缩小而提前释放；原生已分配遥测滞后及非受管占用；缺失/非法输入与自动恢复；最终字段差异拒绝；写前再次配置编辑；实例声明纳入稳定和最终指纹；未知结果遇配置变化仍只读冲突；初次接管及授权范围保护。

补充测试发现并修正了一个不正确的测试预期：8GiB 卡上的旧 8GiB 实例不会因声明改为 4GiB 就释放一半显存。测试现要求旧卡保持占满，额外资源到达后才按新声明扩容；生产算法同时对原生实例声明作保守预留。该次失败属于本地验证，不是现网故障；最终完整回归已通过。

## 原生源码核对与后续

只读核对本地 GPUStack v2.2.3 的 ModelUpdate/ModelSpecBase、模型更新路由及服务实现：原生更新输入需要 name/source 等部署字段。当前实现保留已核对的更新方式，校验只产生 replicas 变更；没有修改 GPUStack 源码。原生没有条件比较后写入接口，不能承诺与外部持续并发修改原子互斥，维护仍需先暂停。

本轮未连接远端、未部署、未发原生模型写请求、未更改数据库或重启 VM/worker/Docker 引擎。尚需独立真实试点核验新资源声明、实例新旧规格与占用、镜像变化期间的副本请求、暂停恢复和未知结果边界，再继续 R4/R5。旧“普通配置变化停写”的实测记录保留为历史，不替代新语义验收。

[方案和运行边界](../../docs/framework/REPLICA-RESOURCE-INPUTS.md) · [暂停接口](../../docs/framework/REPLICA-CONTROL-API.md)
