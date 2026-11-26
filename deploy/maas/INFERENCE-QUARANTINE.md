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

### 常驻试点的发布与回退

测试环境 A 试点使用常驻 Compose 覆盖文件 `compose.canary-admission.json`。API 的自动结清白名单仅含上游路由 `maas-ocr-witness-canary`。发布后同时核对 Compose 渲染值和运行中 API 的白名单。专用项目 `pp-witness-a-test` 使用逻辑模型 `ocr.pp-witness-canary`，API 和执行器从只读文件 `/run/maas-pp-witness-a/inference.key` 加载凭据。按原部署参数重新创建服务时需带上该覆盖文件。

临时退回只读时，清空上述覆盖文件中 API 的 `MAAS_RUNTIME_PROOF_APPLY_ROUTES`，按当前 Compose 配置重建 API 并回读生效值。退役项目时，再撤销其专用 GPUStack 密钥、删除对应 MaaS 绑定和 A 路由对该身份的授权。[持续启用验收](../../docs/framework/E1-DESIGN.md#测试环境-a-路由持续自动结清2026-11-26)

从已提交的版本运行 `python3 scripts/maas/build_worker_bundle.py --source-only --output <发布包>`，解包后用 `scripts/maas/verify_worker_bundle.py --root <解包目录>` 校对文件。源码包沿用目标 worker 的证书和公钥。试点使用单独的签名映像配置标识和固定到一张 GPU 的原生模型部署。发布后从目标 worker 通过认证 HTTPS 回读版本和签名。当前启动许可用过的 worker 容器退出后，通过 E5 同机接续流程换发会话。

Bootstrap 发证服务也需使用能识别 `runtime_witness` 的配置校验模块。如果签名引用的是 OCI 索引，先按 worker 的操作系统和架构解析镜像配置摘要，再与 worker 上的 `docker inspect .Image` 交叉核对。宿主机证明配置的 `image_id` 使用该实际镜像 ID。

控制端切换前把任务库备份恢复到隔离 PostgreSQL，分别用新镜像应用迁移 018、用原镜像重新连接并复核旧的未决活动。现场只重建参与迁移的应用服务。要回退控制端，先将试点副本归零，再按现场保存的原 Compose 参数切回原镜像，保留迁移和活动账本。要回退 worker，在专用映像配置标识下发布旧规则的新签名版本，并按相同的接续流程更新该 worker。该步骤沿用已发布的镜像和现有信任材料。
