# 调节器显式暂停与恢复

2026-11-12 · 候选实现，尚未部署 · [Issue #39](https://github.com/WTaoGitHub/cybercafe-maas/issues/39)

本轮采用整个调节器的暂停开关，不分别暂停每个模型。按模型暂停需要额外处理逐模型状态、容量占用及部分接管，留待有实际需求再设计。本轮不把它登记为已承诺的开发项。

## 最新控制规则

启用 Apply 且未暂停时，副本数是调节器负责维护的输出。人工或其他程序改变数量后，调节器按**当前策略和当前可用 GPU 容量**重新规划；不是恢复最后一次记住的副本数。外部归零可以重新扩容，外部调高可以逐个缩容。要维持人工设置，应先暂停。此决议替代旧候选中“数量漂移后冻结整个 scope、再次 adopt 才恢复”的规则；旧演练记录保留为历史证据。

规划同时保留现有启动预留证明和在途上限。用于缩容的容量计算只加回受管单卡实例的声明显存，且不超过原生已分配显存；非受管占用和系统保留继续扣除。无法核对的删除、分布式、错误落点或资源声明不一致，不凭猜测缩容；此时仅明确的策略最大值可给出数量纠正目标，仍须经过健康检查。CPU offload、跨节点和 LoRA 等原有不支持范围不扩大。

缩容每次 -1，扩容每次 +1，仍只有一个待确认写入。缩容完成须读回目标且实例删除已经收敛。默认保留 `auto_scale_down_min_replicas`（默认 1），并按“GPUStack 可能先删除健康实例”的最坏情况检查服务下限；保护无法满足时返回 `scale_down_service_protected`，等待就绪恢复或运维处置，不强制删最后的健康副本。此处服务保护约束副本目标和健康数量，不代表已实现逐副本请求排空或零中断缩容。可用容量为 0 时，默认仍保留目标 1 等待资源，不承诺存在可服务的健康实例。`execution.scale_down_batch_max` 默认 1，允许设为 0 关闭自动缩容；当前实际写入仍逐个执行。

最终读取阶段发现新一轮外部编辑时，明确未发送的请求撤销本地预留，再用下一轮完整快照重算；不能把这种情况制造成未知写入。GPUStack 更新接口没有比较后写入条件，发送边界仍不能与外部持续写入形成原子互斥，维护操作须先暂停。

计数漂移会更新本地基线和 `last_external_change` 记录，保留冷却时钟；未知写入存在时禁止重定基线、重发或用外部新值掩盖不确定结果。镜像、资源声明、归属、选择器等受保护配置的变化仍阻止写入，须重新接管确认。

新增 GPUStack `models?watch=true` 只读事件流，用于唤醒对账；worker/实例事件流和周期完整读取继续保留。事件内容本身不能授权写入，模型流重连不改变 E17 的观测会话或日报覆盖率。外部编辑数量的事件到达后先合并约 1 秒，再进行完整读取和策略稳定等待。事件丢失时由 `reconcile.interval_s`（默认 30 秒）兜底，另加稳定期与读写耗时；没有固定恢复 SLA。

暂停只禁止自动副本调节；已就绪模型继续服务，任务执行和 E17 采集继续运行。恢复不修改策略模式，不绕过授权、压力、资源、稳定期或未知请求保护。

## 管理接口

独立的 [OpenAPI](../api/replica-regulator.openapi.yaml) 描述以下接口。它们属于 MaaS 运维控制面，不混入客户推理 API 文档；复用 MaaS API 和现有私有 HTTP/HTTPS 入口，不新开监听端口。

| 方法和路径 | 作用 |
| --- | --- |
| `GET /maas/ops/replica-regulator` | 读取全局暂停状态、策略模式、受管部署和未确认写入情况 |
| `POST /maas/ops/replica-regulator/pause` | 持久暂停，成功后不再发出新的副本调节请求 |
| `POST /maas/ops/replica-regulator/resume` | 解除暂停，下一轮重新读取资源和策略后决定动作 |

没有请求体。三个接口均要求独立的 `Authorization: Bearer <operator-key>`；不能使用客户推理 key 或 E17 日报查询 key。未配置时返回 404。不要在无可信网络保护的公网明文 HTTP 上传送运维 key。

```sh
# MAAS_BASE_URL、MAAS_REPLICA_OPERATOR_KEY 由运维在私有环境设置。
curl --fail-with-body -X POST "$MAAS_BASE_URL/maas/ops/replica-regulator/pause" \
  -H "Authorization: Bearer $MAAS_REPLICA_OPERATOR_KEY"
curl --fail-with-body "$MAAS_BASE_URL/maas/ops/replica-regulator" \
  -H "Authorization: Bearer $MAAS_REPLICA_OPERATOR_KEY"
curl --fail-with-body -X POST "$MAAS_BASE_URL/maas/ops/replica-regulator/resume" \
  -H "Authorization: Bearer $MAAS_REPLICA_OPERATOR_KEY"
```

示例返回：

```json
{
  "paused": true,
  "generation": 2,
  "cluster_id": 1,
  "model_ids": [1],
  "policy_mode": "apply",
  "pending_write": false,
  "state_updated": true,
  "native_models_updated": false,
  "scope": "global",
  "pause_gate_open": false,
  "reconcile_checks_required": true
}
```

`pause_gate_open` 只表示暂停开关已打开，不等于此刻所有写入条件都满足；Observe 模式、授权、容量、稳定期、压力和未确认写入保护仍生效。调用本身从不更新 GPUStack 副本数。相同状态的重复操作不增加 generation。

暂停与最终写入发送共用跨进程锁：若正处于发送边界，返回 `409 CONTROL_BUSY`，此次没有确认暂停；应先查询，再重试暂停。成功暂停不能撤销已发送给 GPUStack 的请求，容器启动或删除可能继续完成，原请求的只读确认也会继续。存在未确认写入时允许暂停，但恢复返回 `409 PENDING_WRITE`，不能清空日志来绕过。异常 JSON 位于 `detail.code/message`。

接管仍用私有 `replica_control adopt` 命令完成；接管不再隐式解除已设置的暂停，需显式执行 `resume`。CLI 同样提供 `status/pause/resume`。首次尚未接管时，HTTP 返回 `409 ADOPTION_REQUIRED`。仅配置变更导致 scope 不匹配时仍可紧急暂停，恢复须先重新接管。

## 部署准备

追加 `deploy/maas/compose.replica-control-api.yaml` 至 v2 Apply 的 Compose 文件列表；当前仍运行旧 v1 的环境不能仅加入口就启用本功能。API 与 E17 必须使用同一宿主机私有 journal 目录及其 `state` 子目录。若另设 state 路径，须同步修改两处挂载；路径不同会使暂停失效，部署前必须核验。

运维生成至少 32 字符的随机 key，保存到私有文件，权限 600、目录 700、运行用户 UID 10001 可读。设置 `MAAS_REPLICA_CONTROL_KEY_PATH`；该 key 不写入仓库、不放入镜像、不发给客户。其余路径来自已有私有策略和 journal 配置。API 挂载策略/journal 只读、state 可写，不需要 GPUStack management key。

`edge.nginx.conf` 已列出三个路径；实际入口若采用现场生成的额外白名单，也需同步加入。未部署本轮修改的入口不会自动获得新 API。恢复及策略文件更新通过约 1 秒的本地文件元数据检查唤醒主循环；另保留 1 秒事件合并延迟和策略稳定期，不承诺立即完成启动，也不增加正常情况下对 GPUStack 的轮询频率。

验收至少覆盖：授权与拒绝、全局暂停后人工改动无自动写入、重复暂停/恢复、进程重建后仍暂停、恢复后重新调节、发送边界竞争、未确认请求不能恢复、Observe 不能因恢复变成 Apply。当前为本地候选验证，尚未在常驻环境发布。
