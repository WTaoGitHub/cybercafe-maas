# MaaS 不确定推理的局部隔离

候选实现跟踪 [Issue #2](https://github.com/WTaoGitHub/cybercafe-maas/issues/2) 与 [Issue #62](https://github.com/WTaoGitHub/cybercafe-maas/issues/62)。

MaaS 在发送推理前记录派发 token、逻辑模型和实际配置的上游路由。动态准入模式还固定记录策略中对应的 GPUStack 部署 ID。连接中断、响应结果不确定或发送后的进程丢失仍进入 `UNKNOWN`。同一库中，以下请求被隔离：

- 同一逻辑模型，包括目录条目换了修订或路由后到达的请求。
- 通过同一上游路由的其他逻辑名称。
- 动态模式下同一部署池的其他路由或别名。静态模式按逻辑模型和上游路由识别范围，无法识别不同路由背后的同一部署。此类路由共库时应使用经过验证的同一动态池绑定。

同步在该范围返回 `503 BACKEND_QUARANTINED`。异步队列跳过该范围。若任务已领取但尚未发送，二次检查会将其放回队列，保留原任务、期限和执行重试预算。其他范围可使用剩余容量。未知执行仍占一份全局及项目额度，已被计入活跃任务的相同 attempt 只计一次；没有剩余额度时其他模型也会等候。全局维护暂停和排空的行为沿用原有契约。

### PP-OCRv4 执行保护镜像

在包含 `server.py`、`execution_guard.py`、`image_decode.py` 和 [Dockerfile.execution-guard](../../gpu-ocr/Dockerfile.execution-guard) 的构建上下文中，以固定基础镜像的完整引用作为 `BASE_IMAGE`。已验证的基础镜像摘要为 `sha256:9ec6c93fd35ecf2bfd1a9d842cf7afab4276cff6475e5afe8d56f43acbdca9d9`，先前发布的受保护镜像索引摘要为 `sha256:4a1ba7fa63408f475bcee4e1a7927b165c1ed5333e7cb27c3283e47f22a3a99c`。GPUStack 沿用 `python3 /app/server.py` 入口，按实际部署配置 `MAAS_PP_OCR_EXECUTION_ORIGIN`、`MAAS_PP_OCR_EXECUTION_ROUTE`、`MAAS_PP_OCR_EXECUTION_KEY_FILE` 和 `MAAS_PP_OCR_EXECUTION_CA_FILE`。推理进程读取按 GPU 部署的私有凭据文件。

保留同一 GPUStack 部署和网关路由时，池预算可随其中合格副本数自动变化。先在全部可达副本验证回执再启用目录保护。共享路由的 `UNKNOWN` 按模型池隔离。需逐 GPU 隔离时使用下文的固定路由配置。

## 逐 GPU 隔离候选

动态模式下，容量池可使用 `replica_routes` 将实际网关路由名映射到固定 GPU 的单副本部署。下面是加入一个池的字段示例。池的 `model_id` 为其中一个部署，`routes` 仍保存客户端逻辑名称到目录默认路由 ID 的映射。

```json
"replica_routes": {
  "example-gpu-a": {"route_id": 4, "model_id": 2, "gpu_uuid": "GPU-11111111-1111-1111-1111-111111111111"},
  "example-gpu-b": {"route_id": 5, "model_id": 6, "gpu_uuid": "GPU-22222222-2222-2222-2222-222222222222"}
}
```

- 确认每条路由保留唯一原生部署目标和唯一 GPU，使用同一模型来源、后端版本和推理配置。每个副本都须启用执行保护，所有可达路径都须执行回执检查。核对实际 Higress 目标、重试落点和无回退，再设置 `gateway_verified: true`。为池内所有 MaaS 目录别名设置 `execution_guard: true`，并授权相应的上游凭据访问池内每条路由。改绑或替换路由时先排空，并重新验证网关配置。共享 GPU 仍须登记已联合验证的布局。
- 发送前，MaaS 从新鲜预算中选择可用 GPU，并在原活动记录中写入路由和 `dispatch_gpu_uuid`。执行端启动回执须匹配该 GPU，且提交时仍有执行资格。原承载进程的可信终态回执可以结清该 token。观测器自动增减已登记 GPU 的额度，新增固定 GPU 路由须加入策略后验证。
- 已获权的请求进入 `UNKNOWN` 时，任何绑定到同一 GPU 的路由都停止新派发。其他已验证 GPU 按剩余全局、项目、模型及异步额度接单。仅有发送意图且无可信启动回执时，沿用原模型和部署池保护。缺少路由证据时保持整库保护。无法证明避开受影响 GPU 的路由走保守准入。

## 自动解封的证据

原派发收到该 token 的完整终态响应，或执行保护收到同一 GPU 进程的可信终态回执，可以自动关闭对应的不确定记录。异步传输终态还核对 task ID。数据库记录 `Transport confirmed completion` 或 `GPU confirmed execution ended`。该范围在所有相关 `UNKNOWN` 都结清后自然恢复。已经终结的任务继续保留原状态，解封本身不会重跑任务或补写成功收据。普通成功响应的结果提交和计量仍走既有事务。

健康检查、worker 上下线、关机通知、队列空闲、计时到期及新请求的成功，都不能证明原 token 的执行已经结束。当前没有可靠的上游按请求查询接口。原连接已彻底丢失时，继续保留隔离，由运维根据实际终止或完成证据通过 `resolve-backend` 或 `resolve-sync` 处理。查看 `drain-status` 可核对 token、模型、发送时的路由及部署 ID。

## 共库部署与回退

迁移 `012` 保留旧账本。旧版本写入的记录没有派发路由证据，若仍为 `UNKNOWN` 则保持整库隔离，直至明确解决。候选 API 与全部执行器在一次排空窗口内升级，先暂停、检查在途及不确定活动，再迁移数据库、部署进程、验证双模型探针并恢复。正式共库前还需验证旧入口过渡、现网授权及实际 GPU 布局。不能用换路由或删任务清理不确定活动。

回退时保留 `012` 和活动账本，在维护窗口确认执行状态后恢复。回退到旧准入代码会恢复整库隔离规则，须在隔离环境演练进程版本切换。

## 受保护聚合路由的额度回收

迁移 `017_execution_scope.sql` 保留历史活动。新策略适用于单个受信 Higress 服务的 OpenAI 兼容聚合路由，GPUStack 继续自动放置实例。按模型池配置，模型名称、池数和 GPU UUID 均可变化。

1. 使用**独立、不可变的强制执行保护后端版本**。固定其镜像摘要和启动入口。该路由的首次派发、重试、滚动替换和兜底都必须经过强制守卫；变更到可绕过守卫的版本前须关闭并排空该路由。单次拓扑采样无法保证以后的网关配置。无法维持此部署契约时沿用原策略。PP 使用更新后的 `Dockerfile.execution-guard` 构建，缺少执行凭据会启动失败。Ovis 与 Unlimited 的受保护入口使用同一版 `execution_guard.py`。其他兼容后端复用相同的进程身份、签名探针和执行获权／终态契约。
2. 在原容量 Compose 配置上叠加 `compose.guard-scope.yaml`。设置与控制端代码一致的 `MAAS_TASK_IMAGE`、实际网关容器名 `MAAS_GPUSTACK_CONTAINER`，以及所有者为 MaaS UID 10001、权限 0700 的 `MAAS_GATEWAY_EVIDENCE_DIR`。采集器只读取网关命名空间的回环 Envoy 管理接口，将目的实例及其服务地址写入专用文件。observer 只读挂载此目录，需要读取原有私有 GPU 验证密钥文件，且能访问原生 worker 的服务端口。多个独立网关实例共同承载一条路由时，应先具备覆盖所有这些网关的证据；本版采集器仅覆盖指定的网关。
3. 同步升级数据库、所有 API、执行器和观察器，再在已核验的池上设置 `guard_backend_version` 为上述版本，保留 `gateway_verified: true`，同一路由的所有目录别名均须使用 `execution_guard: true`。观察窗口核对 `guard_topology`、`route_gpus`、`ready_ids` 及模型额度。观察器逐一核对 Higress 的正权重集群及其唯一地址、GPUStack 的运行实例和原进程对随机挑战的签名。缺少证据时，该池不给额度。每次派发引用当时的进程集合，重启产生的新进程不能领取旧授权。
4. 失联、同步到期或执行器租约到期时，共用账本在原事务里关闭未获权的可信派发，记录原因 `Execution closed before any GPU grant`。已经获权的执行继续等待原进程可信终态。若上游返回成功响应但账本没有获权记录，系统记录契约冲突，保留或重新打开隔离。原请求保留结果状态，不会因为释放容量重跑或补写成功计量。
5. 对已获权且有完整执行范围的 `UNKNOWN`，系统根据 GPU UUID 自动隔离。对旧账，运维核对该次请求**所有可能落点**及其物理 GPU 后，可运行 `python -m maas_platform.cli isolate-sync --token <原令牌> --gpu-uuid <原卡UUID> --reason <证据引用>`。异步对应 `isolate-backend`。该操作只记录范围；进程退出、成功和计量字段保持原有含义。找不到物理卡身份或不能排除其他落点时，保留原隔离。不得根据 worker 编号或 IP 猜测 UUID。
6. 同步和异步准入均以健康卡重新计算预算。如果 Higress 仍可能选到故障卡，该聚合路由显示 `gateway_reaches_isolated_gpu`；已验证的独立单卡路由可以跳过故障卡。网关剔除原目的地后，使用签名确认的其余聚合目标恢复接单。故障卡重新出现时重新检查，迟到或越过观测窗口的请求还须在 GPU 获权时通过原进程集合和当前隔离检查。缺少可验证路由的其他池保守停发。原占用仅在有效预算已经排除其物理范围时从动态占用计数中扣除，显式全局、模型或项目硬限额仍约束原占用。查看 `capacity.json` 的有效预算、`isolated_gpus` 和各池原因；`unknown_capacity` 分别给出未结清总笔数、由 GPU 隔离覆盖的笔数和仍单独计入占用的笔数，`drain-status` 保留未结清活动。
7. 常规启用继续走已有维护流程。若旧动态策略仍在服务，所有剩余活动均为落点已核实的 `UNKNOWN`，可用 `capacity-enforce-isolated` 切换到有健康容量的新策略。此入口要求没有正在准备或派发的请求，并在准入锁内复核新快照。该操作不会解除全局维护暂停，设备排空／关机仍要求原有终止证据。
8. 为从零容量开始验证新版，先发布控制端和 worker 签名配置，保留旧活动策略到零点。在完整原生清单与新鲜预算均确认归零后，登记已查实的历史物理落点，载入强制守卫策略，运行 `capacity-enforce-isolated-zero`。该命令沿用动态模式、活动与证据校验，还要求每个配置池都启用强制守卫，其有效预算和可授权进程集合均为空。它会拒绝过期或缺失的快照，并记录零点切换事件。新 worker 的进程证明与稳定性检查通过后沿用同一策略自动升额。变更模型镜像摘要时，须先将摘要与入口对应的资源档位及执行凭据挂载规则加入该母版使用的签名 E5 配置，并通过配置服务回读验签。


使用独立 PostgreSQL 的验收命令为 `PYTHONPATH=.:plugins/gpustack-replica python -m pytest -q tests/maas/test_execution_scope_integration.py tests/maas/test_gateway_scope.py`。`MAAS_TEST_DATABASE_URL` 必须只指向可销毁且名称以 `_test` 结尾的数据库，集成夹具会重建 schema。[故障隔离跟踪](https://github.com/WTaoGitHub/cybercafe-maas/issues/2)

## 持久结束回执与控制端主动对账

PP-OCRv4 试点使用带执行守卫的镜像。将独立持久卷的每卡子目录以可写方式挂载到对应 GPU 的推理容器，设置 `MAAS_PP_OCR_EXECUTION_RECEIPT_DIR` 为该容器内的绝对路径。目录需预建，只允许原 worker 写入，由控制端读取和确认删除（例如专用组、目录 2770、回执 0640）。控制端所见布局为 `<root>/<GPU UUID>/<task 或 sync>.<token>`。worker 只挂载自己的子目录。可使用两端都能访问、支持同目录原子替换和 fsync 的私有持久卷，先在所选存储实测容器替换和断连。已有模型权重卷继续使用原有权限。

执行端在引擎确认结束后先写入带 GPU 签名的回执，控制端已确认常规回调后即可删除。文件只含原派发身份与终态。两次回调均丢失时保留记录。存储写入失败仍走原回调并记录告警。若两种途径的证据均丢失，继续隔离。运维可在 worker 已退出后从持久卷找回同一条记录。轮换 GPU 密钥前应处理用旧密钥签发的未决回执。

在控制端基础 Compose 上叠加 `compose.receipt-reconcile.yaml`，设置 `MAAS_RECEIPTS_ROOT`、`MAAS_PP_OCR_KEYS_PATH`（现有私有 GPU 密钥映射）、`MAAS_TASK_IMAGE` 和 `MAAS_RECEIPT_ROUTE`（准确的上游路由）。启动其中的 `receipt-reconcile` 服务。默认 `observe` 模式读库、验签，日志的 `verified` 表示可结清，保留文件和数据库活动。现场对照通过后以 `MAAS_RECEIPT_MODE=apply` 重新创建该服务。单次核查可使用同一命令加 `--once`。回退时停止该服务或切回 observe，未决活动沿用既有隔离。单池试点通过后才逐个启用其他守卫模型相应前缀的 `RECEIPT_DIR`，每个上游路由需显式加入对账范围。

控制端在推理进程之外串行查证，按批限速，空闲或不可达时退避至 15 秒。在事务内重查原执行范围、token、路由、GPU 和获权进程。匹配的 `UNKNOWN` 转为 `COMPLETED`，原回执提交与主动查证同时发生时仅结清一次。冲突、错误签名、其他进程上报和缺少原始范围的旧活动保留待核。运行中、查无记录、重新 Ready 均不足以释放。日志 `recovered` 统计主动结清，`mismatch`、`invalid_or_unreadable`、`unavailable` 需要核查证据、挂载或数据库连接。执行器和用户任务的成功结果仍走原提交流程。

项目级任务详情的 `execution_state` 给出最近一次领取的后台活动状态。`drain-status` 列出活动的派发年龄与证据原因。新鲜且已启用的动态预算会在 observer 的 `metrics.prom` 中发布 `maas_unknown_capacity_charged_as_occupancy`、`maas_unknown_capacity_covered_by_gpu_exclusion` 与 `maas_unknown_capacity_excluded_slots`。按采样间隔积分第一项可估算单独占用的槽秒，结合每池 `capacity.json` 的 reason、等待队列和真实派发核对业务影响。缺测时保留观测空档。

独立库验收使用 `tests/maas/test_execution_scope_integration.py` 和 `tests/maas/test_pp_ocr_execution_integration.py`（数据库与启动命令见上节）。50 笔回执用例各丢弃两次完成回调，在同步和异步入口核对已获权、失联、观察、结清及重复证据。该用例使用模拟引擎、真实 PostgreSQL 和本地文件卷；打印从直接发起扫描至提交完成的时延。常驻扫描等待、现场共享卷及真实 GPU 端到端时延需在目标部署另测。

### 从未获权的异步任务重试

执行器可通过 `MAAS_RETRY_UNSTARTED_MODELS` 指定逗号分隔的逻辑模型名，默认为空。按模型独立于回执对账启用。网络中断、部分临时网关响应或执行器租约丢失时，须由强制守卫在原事务中证实从未获权并封闭旧 token，模型修订一致、期限和尝试预算均有效，才将原任务送入 `RETRY_WAIT`。新执行器领取同一任务并重新申请容量。关闭此开关阻止新的故障尝试被归队。已经发生的执行即使随后遇到可重试的存储故障也只结算原任务。使用 `test_transient_failure_before_grant_retries_on_new_token` 与 `test_unstarted_retry_boundaries` 验证允许重试及必须停止的分支。
