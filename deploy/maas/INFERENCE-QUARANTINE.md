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

## worker 本机补报与 PostgreSQL 结清

已启用执行守卫的模型可按镜像选择本机补报。常规结束回调丢失确认（包括 TLS 对端提前关闭）时，推理进程立即用原 token 再报一次。两次均失败时，已落盘的签名回执留在宿主机。独立的 worker 定时器读取原回执，用该 GPU 的凭据向签名配置中的 MaaS HTTPS 入口重送。控制端按原 GPU、路由、进程和尝试核验，在既有 PostgreSQL 事务内结清活动。worker 收到 HTTP 204 才删除相应文件。重复上报保留第一次结束时间。

无盘 worker 的本机目录位于 `/run/maas-bootstrap/execution/receipts/`，可以跨推理容器及 GPUStack worker 容器替换保留。整台 VPC 关停或回收会丢失尚在 tmpfs 上的文件。若进程在生成回执之前崩溃，或文件已经丢失且控制端也未收到终态，活动继续按 `UNKNOWN` 隔离。补报只恢复并发资格，客户任务已失败时保留失败状态和原有计量。

启用方法见[克隆配置说明](../bootstrap/clone/README.md)。补报服务日志按轮统计 `acknowledged`、`unavailable`、`rejected` 和 `invalid_or_unreadable` 等结果。持续拒绝时检查 GPU 密钥、路由和原进程身份。轮换凭据、变更回执目录或下线 worker 前先核对未决文件。暂停补报可停止 timer，必要时等待当前 oneshot 完成，保留文件待后续确认。该路径通过 MaaS API 写 PostgreSQL，控制端无需挂载 worker 磁盘。

## 持久结束回执与控制端主动对账

PP-OCRv4 试点使用带执行守卫的镜像。将独立持久卷的每卡子目录以可写方式挂载到对应 GPU 的推理容器，设置 `MAAS_PP_OCR_EXECUTION_RECEIPT_DIR` 为该容器内的绝对路径。目录需预建，只允许原 worker 写入，由控制端读取和确认删除（例如专用组、目录 2770、回执 0640）。控制端所见布局为 `<root>/<GPU UUID>/<task 或 sync>.<token>`。worker 只挂载自己的子目录。可使用两端都能访问、支持同目录原子替换和 fsync 的私有持久卷，先在所选存储实测容器替换和断连。已有模型权重卷继续使用原有权限。

执行端在引擎确认结束后先写入带 GPU 签名的回执，控制端已确认常规回调后即可删除。文件只含原派发身份与终态。两次回调均丢失时保留记录。存储故障仍走原回调并记录告警。每个执行进程最多一笔回执 I/O 在途，正常路径等待落盘最多 250 毫秒。卷卡顿时先走原回调，后台写入若稍后完成，对账器可清走已确认的回执。若两种途径的证据均丢失，继续隔离。运维可在 worker 已退出后从持久卷找回同一条记录。轮换 GPU 密钥前应处理用旧密钥签发的未决回执。

在控制端基础 Compose 上叠加 `compose.receipt-reconcile.yaml`，设置 `MAAS_RECEIPTS_ROOT`、`MAAS_PP_OCR_KEYS_PATH`（现有私有 GPU 密钥映射）、`MAAS_TASK_IMAGE` 和 `MAAS_RECEIPT_ROUTE`（准确的上游路由）。启动其中的 `receipt-reconcile` 服务。默认 `observe` 模式读库、验签，日志的 `verified` 表示可结清，保留文件和数据库活动。现场对照通过后以 `MAAS_RECEIPT_MODE=apply` 重新创建该服务。单次核查可使用同一命令加 `--once`。回退时停止该服务或切回 observe，未决活动沿用既有隔离。单池试点通过后才逐个启用其他守卫模型相应前缀的 `RECEIPT_DIR`，每个上游路由需显式加入对账范围。

控制端在推理进程之外串行查证，按批限速，空闲或不可达时退避至 15 秒。在事务内重查原执行范围、token、路由、GPU 和获权进程。匹配的 `UNKNOWN` 转为 `COMPLETED`，原回执提交与主动查证同时发生时仅结清一次。冲突、错误签名、其他进程上报和缺少原始范围的旧活动保留待核。运行中、查无记录、重新 Ready 均不足以释放。日志 `recovered` 统计主动结清，`mismatch`、`invalid_or_unreadable`、`unavailable` 需要核查证据、挂载或数据库连接。执行器和用户任务的成功结果仍走原提交流程。

项目级任务详情的 `execution_state` 给出最近一次领取的后台活动状态。`drain-status` 列出活动的派发年龄与证据原因。新鲜且已启用的动态预算会在 observer 的 `metrics.prom` 中发布 `maas_unknown_capacity_charged_as_occupancy`、`maas_unknown_capacity_covered_by_gpu_exclusion` 与 `maas_unknown_capacity_excluded_slots`。按采样间隔积分第一项可估算单独占用的槽秒，结合每池 `capacity.json` 的 reason、等待队列和真实派发核对业务影响。缺测时保留观测空档。

独立库验收使用 `tests/maas/test_execution_scope_integration.py` 和 `tests/maas/test_pp_ocr_execution_integration.py`（数据库与启动命令见上节）。50 笔回执用例各丢弃两次完成回调，在同步和异步入口核对已获权、失联、观察、结清及重复证据。该用例使用模拟引擎、真实 PostgreSQL、本地文件卷和独立常驻对账进程。从回执卷重新可见起，按首次观察到数据库已提交的时间上界报告 P50、P95 和最大值，包含扫描等待。现场共享卷与真实 GPU 端到端时延需在目标部署另测。

### 从未获权的异步任务重试

执行器可通过 `MAAS_RETRY_UNSTARTED_MODELS` 指定逗号分隔的逻辑模型名，默认为空。按模型独立于回执对账启用。网络中断、部分临时网关响应或执行器租约丢失时，须由强制守卫在原事务中证实从未获权并封闭旧 token，模型修订一致、期限和尝试预算均有效，才将原任务送入 `RETRY_WAIT`。新执行器领取同一任务并重新申请容量。关闭此开关阻止新的故障尝试被归队。已经发生的执行即使随后遇到可重试的存储故障也只结算原任务。使用 `test_transient_failure_before_grant_retries_on_new_token` 与 `test_unstarted_retry_boundaries` 验证允许重试及必须停止的分支。

## 长期 UNKNOWN 的原宿主机核查

PP-OCRv4 试点使用迁移 018。原结束回执仍可按既有路径结清。补报之外，MaaS 按原获权进程向 worker 下发带随机挑战的核查任务。worker 复用执行回调的私有 HTTPS 路径领取任务和提交证据。旧任务没有获权时的宿主机绑定，留给原回执或人工证据处理。

1. **控制端。** 使用新版 MaaS 镜像。保持私有 TLS 回调位置允许 POST，客户入口沿用现有边界。将新的 0600 私密 JSON 密钥表放入 API 专用 secret 挂载，用 \`MAAS_RUNTIME_PROOF_KEYS_FILE\` 指向它。表键是 GPU UUID，每张卡使用独立的 32 字节随机密钥，以 64 位小写十六进制表示。这组密钥与容器执行密钥分开。先留空 \`MAAS_RUNTIME_PROOF_APPLY_ROUTES\`，核查只写入审计和调度元数据。
2. **worker。** 在隔离的 Linux Docker worker 安装 \`maas-pp-runtime-witness.service\` 和 worker bundle，配置根用户可读的 \`/etc/maas/pp-runtime-witness.json\`，字段为 \`gpu_uuid\`、准确的上游 \`route\`、容器 \`docker inspect .Image\` 对应的 \`image_id\`、现有执行回调的 HTTPS \`origin\`、\`ca_file\`、该卡的宿主机 \`key_file\`、\`socket\`。将 \`socket\` 设为 \`/run/maas-pp-runtime/evidence.sock\`。非 root PP 容器以 \`socket_gid\` 访问，只给该进程此组权限。先安装并启动 witness，核对 socket 和 TLS 后，再启动试点 PP 容器。服务端需要原生 cgroup v2、主机 Docker CLI 和 NVIDIA 进程查询。
3. **PP 容器。** 用新版守卫构建并固定镜像摘要。对签名运行时规则中准确匹配该镜像和 \`python3 /app/server.py\` 的试点条目加入 \`runtime_witness: true\`。固定版本的 Docker worker 在分配 CUDA 设备前将该容器切为非特权模式，并只读挂载 socket 所在目录。试点必须固定到单张 GPU。专用验收容器可以同样以只读方式挂载该目录，并设置 \`MAAS_PP_OCR_EXECUTION_RUNTIME_SOCKET=/run/maas-pp-runtime/evidence.sock\`。宿主凭据和 Docker socket 仅供 witness 使用。容器内应使用 PP 的进程内引擎，且保持普通容器的权限和进程隔离。
4. **先比对证据。** \`drain-status\` 显示后台活动待核时长、最近探查结果和证据引用。进入 \`UNKNOWN\` 30 秒后按原运行实例合并核查。witness 每 5 秒向现有私有入口询问，连续未答复的挑战退避至 5 分钟。witness 恢复连接后可提前领取下一次核查，已发出的挑战仍须答复或到期。原启动会话、Docker ID、原始 cgroup 和 GPU 进程核对齐备才接受终止。只看到原进程存在、查询失败或启动会话变化时保留隔离。还在运行的 PP 进程仅报告存在，沿用原回执来证明单个请求已经结束。
5. **启用与回退。** 先比较宿主机事件和账本，再把允许自动结清的准确上游路由写入 \`MAAS_RUNTIME_PROOF_APPLY_ROUTES\`。复用已经验证的逐卡路由，或等待实际 Higress 目标确认排除故障卡后再验证健康副本接单。回退时清空该路由开关并恢复 API 进程配置，保留审计。原始结果、历史 \`UNKNOWN\` 和旧的同步异步行为按原有契约处理。

隔离 PostgreSQL 可用 \`tests/maas/test_runtime_recovery.py\`，核对同步异步各 10 笔、错配、过期、重复挑战、迟到原回执及固定 GPU 路由绕行。三台真实 GPU 验收按 E1 设计记录网关实际目标、时延和真实同步／异步请求。故障注入使用可销毁试点副本，沿用项目对 XZ-31-001、XZ-31-002 的整机限制。

### A 试点的发布与退役记录

早期试点通过 `compose.canary-admission.json` 为 `pp-witness-a-test` 单独开放 `ocr.pp-witness-canary`。该试点已于 2026-11-26 排空下线。当前覆盖文件仍承载共享服务设置，已移除 A 的自动结清条目和专用凭据挂载。部署时沿用当前 Compose 配置。测试环境现在按模型目录和 `MAAS_REQUIRE_MODEL_RECOVERY=1` 对共享 PP、Ovis 和 Unlimited 启用自动结清。[下线实测](../../results/maas-e1-model-recovery-20261126/REPORT.md#a-下线后的现场核查)

原试点的单路由放行与凭据范围见[持续启用验收](../../docs/framework/E1-DESIGN.md#测试环境-a-路由持续自动结清2026-11-26)。当前生效路由可在运行中的 API 通过 `Settings.from_env().automatic_runtime_routes()` 回读。

从已提交的版本运行 `python3 scripts/maas/build_worker_bundle.py --source-only --output <发布包>`，解包后用 `scripts/maas/verify_worker_bundle.py --root <解包目录>` 校对文件。源码包沿用目标 worker 的证书和公钥。试点使用单独的签名映像配置标识和固定到一张 GPU 的原生模型部署。发布后从目标 worker 通过认证 HTTPS 回读版本和签名。当前启动许可用过的 worker 容器退出后，通过 E5 同机接续流程换发会话。

Bootstrap 发证服务也需使用能识别 `runtime_witness` 的配置校验模块。如果签名引用的是 OCI 索引，先按 worker 的操作系统和架构解析镜像配置摘要，再与 worker 上的 `docker inspect .Image` 交叉核对。宿主机证明配置的 `image_id` 使用该实际镜像 ID。

控制端切换前把任务库备份恢复到隔离 PostgreSQL，分别用新镜像应用迁移 018、用原镜像重新连接并复核旧的未决活动。现场只重建参与迁移的应用服务。要回退控制端，先将试点副本归零，再按现场保存的原 Compose 参数切回原镜像，保留迁移和活动账本。要回退 worker，在专用映像配置标识下发布旧规则的新签名版本，并按相同的接续流程更新该 worker。该步骤沿用已发布的镜像和现有信任材料。

同一 GPU 同时承载 A 与共享 PP 时，每张卡运行一个宿主机证明进程。worker 的私有配置可用 `routes` 对象逐项指定「上游路由名 → Docker 镜像 ID」，替代单路由的 `route` 和 `image_id`。原单路由配置仍可使用。签发运行身份时，证明进程从套接字对端 PID 找到容器，同时核对 Docker 记录的路由环境变量、镜像 ID 和非特权隔离条件。一次轮询可以分别回答该卡各登记路由的挑战。

只读故障验收时，先记录原派发的 GPU、守卫进程身份和宿主机 Docker 完整容器 ID。在 API 环境运行 `python -B scripts/maas/runtime/inspect_pp_execution.py <任务ID> <尝试token> <上游路由> <GPU-UUID> <守卫进程实例> <原容器完整ID>`。原进程仍在时应读到 `state=UNKNOWN`、`host_outcome=present` 且无终止证明。原进程退出后，同一命令应读到 `host_outcome=terminated`、`signature_verified=true`；只读路由继续显示 `UNKNOWN`。核查器同时匹配原始派发范围、实际获权进程和宿主机绑定，可在不提交状态变更的情况下重复运行。[核查器](../../scripts/maas/runtime/inspect_pp_execution.py)

同一笔已失败任务可能先后收到原进程的结束回执和宿主机的终止证明。回执先结清时，后台活动保留 `COMPLETED`。终止证明先结清时，活动保留 `RESOLVED`，迟到的有效回执可以登记 GPU 结束时间。两条路径均只释放一次并发，重复证据保留已登记的时间和原客户结果。[两种顺序与三模型联合验收](../../results/maas-e1-joint-unknown-20261126/REPORT.md)

### 三模型的账本采样

测试控制端每五分钟在 API 容器中按逻辑模型采样。当前启用的实例为 `ocr.default`、`ocr.ovisocr3`、`document.unlimited-ocr-2`。例如，使用 `systemctl is-active maas-model-recovery-observe@ocr.default.timer` 检查共享 PP 定时器，用 `journalctl -u maas-model-recovery-observe@ocr.default.service --since '24 hours ago' -o cat` 读取样本。A 专用定时器 `maas-pp-canary-observe.timer` 已禁用。[采样脚本](../../scripts/maas/runtime/observe_pp_settlement.py) · [service 模板](systemd/maas-model-recovery-observe@.service) · [timer 模板](systemd/maas-model-recovery-observe@.timer)

每条 JSON 记录包含该模型的自动结清开关、全局维护暂停标记、容量快照中的就绪并发位，以及同步和异步各自的未决笔数、最老未决时长和已持续至少 15 分钟的笔数。滚动 24 小时统计以采样时仍保留的账本为准，包括派发、新增 `UNKNOWN`、自动结清、终止签名与原活动不匹配的笔数，以及故障结清后关联了成功计量的笔数。后两项出现非零值时应核对原活动。每类至少有 10 笔通过验签的结清记录才发布 `signed_p95_seconds`。它从进入 `UNKNOWN` 算到结清，包含 worker 失联的时间。就绪并发位为 `null` 表示没有可用的新鲜快照。采样验签及 P95 的本地回归命令为 `python -m pytest -q tests/maas/test_pp_settlement_observation.py`。

### 共享 PP 路由按 GPU 撤出和恢复

容量观察器可通过 `MAAS_PP_GATEWAY_INTENT_FILE` 发布守卫模型的候选副本。它沿用已有模型版本、GPU 身份、就绪状态和同卡显存校验，并从 PostgreSQL 的 `inference_slots` 读取正在隔离的 GPU。文件通过同目录替换发布。将文件所在的专用目录只读挂载给网关协调器，两个进程使用具有相应文件权限的 UID。

在内置 Higress 的网络命名空间中，用 GPUStack 镜像自带的 Kubernetes 客户端运行以下模块。镜像或只读挂载需包含仓库的 `scripts/maas/runtime/reconcile_pp_quarantine.py` 和 `reconcile_gateway_capacity.py`。路由 ID、模型 ID 和名称取自目标环境，以下为共享 PP 的测试配置。

```sh
python -B -m scripts.maas.runtime.reconcile_pp_quarantine \
  --route-id 1 --model-id 1 --model-name maas-ocr \
  --intent /run/maas-pp-routing/intent.json --kubeconfig /run/pp-kubeconfig
```

协调器只维护所选路由的 `maas-pp-quarantine-<route-id>` EnvoyFilter。它逐项核对原生 ingress 的正权重、服务注册表地址和候选副本，把隔离 GPU 上的全部副本移出。剩余原生单端点集群保留相对权重和已有重试设置。当前守卫池只允许一个原生模型，出现独立回退入口时返回本地 503。AI 代理出现未经支持的重派策略、候选文件超过 30 秒或没有合格副本时，同样关闭上游派发。协调器中断时保留最近一次过滤规则。MaaS 继续独立读回 Envoy 生效配置和端点，并要求剩余进程通过签名挑战。

部署时先更新容量观察器及网关证据读取器，确认候选文件持续刷新，再启动协调器。核对主路由所有路径、隔离卡的同卡副本及实际重试计数。结清后应重新观察到原生路由和健康容量恢复。回退时停止协调器，核对自有过滤器的标签和 UID 后删除该资源，确认 Envoy 已恢复原生目标并撤下候选文件输出。A 与共享 PP 的自动结清白名单独立于这个过滤器。

本地回归可运行 `python -m pytest -q tests/maas/test_pp_route_quarantine.py tests/maas/test_gateway_scope.py tests/maas/test_gateway_capacity.py`。测试覆盖同卡多个副本、零权重、注册表与端口变化、候选过期、回退和 AI 代理重派。[实机验收记录](../../results/maas-e1-shared-pp-gpu-exclusion-20261126/acceptance.json)

### UNKNOWN 后撤路与实测停派时间

迁移 `019_quarantine_notifications.sql` 在同步或异步执行记录的隔离 GPU 发生变化时发出 PostgreSQL 通知。事务提交后，启用了 `MAAS_PP_GATEWAY_INTENT_FILE` 的容量观察器重新读取完整隔离集合。已知拓扑可立即追加撤路并保留原采样时间；恢复目的地需要新的拓扑采样。随后观察器在 30 秒内以最长一秒的采样间隔核对网关和进程。Envoy 路由读回、进程签名和原有的稳定期继续决定何时恢复准入。监听重连会主动重读，定时全量采样也会校正丢失的通知。

在测试环境使用独立的 `test_agent_pp_*` 项目，同时向共享入口发送同步和异步请求。以下命令使用测试控制端的 `MAAS_DATABASE_URL`。在发流量前安装探针，在故障期间或收到原进程签名终止证明后读取窗口，并在导出全部样本后卸载。

```sh
python -m scripts.maas.runtime.pp_grant_window install "$TEST_PROJECT"
python -m scripts.maas.runtime.pp_grant_window traffic "$TEST_PROJECT" --url "$MAAS_TEST_API_URL" --seconds 300
python -m scripts.maas.runtime.pp_grant_window read "$TEST_PROJECT" --kind sync --token "$FAULT_TOKEN"
python -m scripts.maas.runtime.pp_grant_window remove "$TEST_PROJECT"
```

探针在数据库写入 GPU 授权的同一事务中记录时间。每类流量分别从原任务的 `unknown_since` 计时，取故障后新派发且最先在健康卡获得授权的请求。异步排队回执不参与计算。窗口包含探针发送间隔和执行器取单延迟，因此表示这组流量实际等到新授权的时间。探针还会检查整个 UNKNOWN 期间的隔离卡授权，包括故障前已派发、随后才申请 GPU 的请求。网关重试另用 Envoy 生效路由和分集群尝试计数验证。

2026-11-26 在测试环境对 worker 32 做了前后各 20 次相同故障。按上述口径，同步停派 P95 从 18.10 秒降至 10.54 秒，异步从 17.76 秒降至 10.05 秒。此结果适用于共享 PP 自身产生且带有执行范围证据的 UNKNOWN。A 专用单卡路由产生的 UNKNOWN 有独立跟踪记录。[40 次逐笔实测](../../results/maas-e1-shared-pp-grant-latency-20261126/acceptance.json) · [A 专用路由问题 #85](https://github.com/WTaoGitHub/cybercafe-maas/issues/85)

迁移 `020_pinned_gpu_quarantine.sql` 支持 A 与共享 PP 位于同一物理卡的情形。固定路由上的原请求进入 UNKNOWN 时，数据库核对派发 GPU、获得授权的 GPU 和进程，以及宿主机签名登记的原路由和运行身份。记录相符、执行守卫生效且无冲突时，把该卡写入原活动的隔离记录。观察器随后撤下共享入口的同卡副本。已有 UNKNOWN 使用相同条件重新核对，保留首次进入 UNKNOWN 的时间和人工隔离证据。同一卡所有相关活动都结清后，网关和容量观察器重新验证副本。缺少独占落点证据时继续保守阻断。

2026-11-26 在 worker 34 的 A 专用路由上制造了同步、异步各 10 次进程退出。每次都暂缓宿主机回传终止证明，直到共享入口的两类请求在健康卡获得 GPU 授权。20 个窗口中，首次新授权的 P95 / 最大值为同步 5.32 / 5.34 秒、异步 4.56 / 4.62 秒。窗口内获得授权并最终成功的请求有同步 601 笔、异步 632 笔。隔离卡新增授权为零，40 次实测网关重试都落在健康副本。现场未配置活跃回退上游。恢复证明后 20 笔原请求全部通过验签结清，成功计量为零。另在共享路由隔离 worker 32 做了一次对照，worker 31、34 在原任务结清前均获得授权。修复前的两次 A 故障在整个 UNKNOWN 期间都未取得健康卡新授权，无法计算修复前的 P95。[逐笔结果与采样口径](../../results/maas-e1-pinned-pp-isolation-20261126/acceptance.json)

### 同卡 Linux 启动交接观察

给独立测试集群的 MaaS API 设置 `MAAS_BOOT_OBSERVE_CLUSTER` 后，控制端可记录已确认的启动会话，以及因同卡重启而具备结清证据的执行。这个阶段只写观察记录。原回执和原进程证明继续按原有策略处理。

先在 GPUStack PostgreSQL 安装 `scripts/maas/runtime/boot_observe_native.sql`，在任务库运行迁移 022。控制端安装 `observe_boot_sessions.py`、`maas-boot-observe.service` 和同名 timer，在 `/etc/maas/boot-observe.env` 中设置目标集群和本机私有的 `MAAS_BOOT_APPROVAL_ROOT`。启用 timer 后，使用 `journalctl -u maas-boot-observe.service -o cat` 查看注册和候选计数。worker 需要能处理 `boot_check` 的宿主机 witness。它通过原私有 HTTPS 回调签署一次性挑战，未部署模型时也能作答。

原生注册提交时记录顺序号。控制端用当前唯一注册的集群、卡、worker 和会话匹配 Bootstrap 已发许可、该启动的密钥承诺和见证签名，在收件后再次核对当前原生注册。首次确认建立基线。同一启动重新注册保留代次，已替代的启动不能重新推进代次。获权时只有可信启动匹配才在该笔执行上记会话。未登记启动密钥的老 worker、上线前的既有授权，以及不同 GPU 上的新启动都不足以产生候选。

以下查询可用于三台新 worker 到达时逐笔核对。序号和签名存于同一条启动历史，`boot_unknown_candidates` 保留曾观察到的候选，`current_boot_unknown_candidates` 只显示仍未结清的候选。

```sql
SELECT cluster_id, gpu_uuid, generation, boot_id, worker_id, session_id, confirmed_at
FROM boot_session_history ORDER BY cluster_id, gpu_uuid, seq;

SELECT c.kind, c.token, c.gpu_uuid, o.boot_id AS old_boot, n.boot_id AS new_boot,
       c.unknown_since, c.observed_at
FROM boot_unknown_candidates c
JOIN boot_session_history o ON (o.cluster_id,o.gpu_uuid,o.seq)=(c.cluster_id,c.gpu_uuid,c.old_seq)
JOIN boot_session_history n ON (n.cluster_id,n.gpu_uuid,n.seq)=(c.cluster_id,c.gpu_uuid,c.new_seq)
ORDER BY c.observed_at;
```

可在独立的 `*_test` PostgreSQL 设置 `MAAS_TEST_DATABASE_URL`，运行 `python -m pytest -q tests/maas/test_boot_observation.py`。用例会重建测试库的 public schema，覆盖原生注册提交、重复签名、并发观察、两种超时顺序和控制进程在提交前退出。[E1 跟踪](https://github.com/WTaoGitHub/cybercafe-maas/issues/2)
