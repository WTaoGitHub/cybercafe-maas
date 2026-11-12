# 调节器显式暂停与自动纠正本地验收

2026-11-12 · 本地候选通过，未部署 · [Issue #39](https://github.com/WTaoGitHub/cybercafe-maas/issues/39)

用户决议：启用中按现状/策略纠正外部数量修改；暂停中不发新调节动作；调高后也自动缩回并保留服务保护。针对逐模型暂停的复杂度提问，本轮按已说明的全局暂停推进，未实现逐模型状态。

后续确认（2026-11-12）：用户明确同意“先做整个调节器的暂停、恢复，后续再扩展按模型控制”。已将逐模型扩展登记至原 Issue #39，不改变本轮全局实现和未部署状态。

## 交付

- 三个管理员 HTTP 接口：状态、暂停、恢复；独立 Bearer，不接受客户推理 key 或 E17 查询 key。默认关闭，挂载正确私有策略、控制状态和密钥后启用；无需将原生管理 key 暴露给 API。
- 暂停/恢复持久、幂等，与最终发送共享锁。忙返回 409；Pause 可以保留未知请求，Resume 拒绝未确认请求。接管不隐式恢复；Observe 不因 Resume 获得 Apply 权限。
- 外部数量修改刷新观察基线、保留冷却时钟、记录最近漂移。重新规划目标而非恢复历史数；只读回旧值或中途外部编辑不能掩盖未确认写入。
- 有界逐个缩容，确认原生实例删除后再继续；默认最低服务目标 1 和最坏情况健康保护。无法保证健康下限时停在 service_protected，不冒充已完成纠正。
- 总容量证明只加回受管声明显存，非受管占用继续扣除。新增启动仍使用原有余量和整卡预留证明；假想重新摆放不能授权额外启动。异常/未核对落点仅允许策略显式最大值给出纠正目标，仍检查健康。
- models 事件唤醒及约 1 秒本地控制文件变化检查；完整管理快照仍是写入依据。E17 两类数据订阅及会话口径不变，旧 v1/插件规划未改。

## 验证

专项合并：**141 passed / 3 skipped**；客户 OpenAPI 示例及容器入口回归：**18 passed**。保留一个既有 Starlette/AnyIO 弃用警告。3 条跳过分别需要固定 GPUStack SDK 或独立测试数据库，不计作真实集成通过。

覆盖授权拒绝、接口关闭、持久暂停、重复调用、锁竞争、PendingWrite、scope 变更；现有 Ready GPU 无新事件扩容；人工清零后相同动作重新确认；超量逐个缩至当前容量；策略最大值内的资源不足缩容；最低目标/健康保护；删除未收敛及超时只读确认；写前策略/暂停/外部目标再次变化；Observe 保持零写入；原生元数据在接管后补齐仍可确认；多模型预算和非受管占用；模型事件取消清理；旧 v1 和插件回归。

```sh
PYTHONPATH=.:plugins/gpustack-replica .venv-maas/bin/python -m pytest \
  tests/maas/test_gpu_daily*.py tests/maas/test_replica_controller.py \
  tests/maas/test_replica_apply.py tests/maas/test_replica_observe.py \
  tests/maas/test_replica_contract.py tests/maas/test_replica_reconcile.py \
  tests/maas/test_replica_writer_v2.py tests/maas/test_replica_trial.py \
  tests/maas/test_replica_control_web.py tests/maas/test_replica_targets.py \
  plugins/gpustack-replica -q
PYTHONPATH=.:plugins/gpustack-replica .venv-maas/bin/python -m pytest \
  tests/maas/test_openapi_examples.py tests/maas/test_container_ingress.py -q
```

## 后续实测与边界

本轮无 SSH、原生模型 PUT、镜像构建、部署、重启或数据库变更。上次已恢复到原 E17/v1 的真实状态记录保留；它不能证明本轮新语义已经上线。新接口默认不启用，旧版入口不会自动出现本能力。

独立试点需补验：实际 API/E17 共享状态、模型事件、人工调低后自动恢复、调高后自动缩回、原生删除收敛、健康下限保护、暂停跨进程保存、未知结果不能恢复；继续既有隔离通道/在途不确定结果和受控冷缓存试验，之后再 R5 发布。不能以本地替身测试替代真实 GPU 实测。部署前须核对 operator 凭据、私有挂载、入口白名单、v2 策略/授权及原插件 Observe，无 VM/worker/Docker 引擎重启要求。

操作示例、响应和配置见[控制说明](../../docs/framework/REPLICA-CONTROL-API.md)与[OpenAPI](../../docs/api/replica-regulator.openapi.yaml)。
