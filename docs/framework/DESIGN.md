# 统一模型服务框架设计

版本：0.1 · 2026-10-20 · 状态：设计提案，尚未实施  
依据：[现有规划](../../MAAS-PLAN.html)、[原 milestones](../../MILESTONES.md)、[部署代码](../../deploy-gpustack.sh)、[GPUStack 管理代码](../../gpustack_admin.py)。

## 1. 目标与范围

将分散且可能退出的 GPU 节点组织为统一模型服务。客户通过稳定模型标识提交请求；框架统一处理身份、任务、状态、结果、容量、故障与计量。模型只实现能力适配器，业务核心不依赖 OCR、LLM 或图像生成的具体算法。

第一版交付：异步文件型推理主链路、同步请求及流式请求的能力声明、节点排空、持久任务、结果查询、可观测性。用现有 OCR 和不占 GPU 的模拟后端验收。模拟后端支持延迟、进度、故障和进程中断，用来验证框架，而不是继续选模型。

不在首版实现：计费结算、多地域/控制端高可用、自动识别用户是否上机、跨节点分布式推理、所有模型通用 JSON 请求体、视频服务或训练服务。

## 2. 架构决策

| 决策 | 第一版选择 | 理由与边界 |
|---|---|---|
| 部署 | 001 控制端常在线，同时保留 GPU worker；002 为可退出 worker | 沿用已验证部署；001 仍是单点，重启期间不承诺可用 |
| 应用结构 | 一个代码库，API、执行器、协调器三个进程角色；Docker Compose 管理新增服务 | 简化交付，不拆大量微服务；GPUStack 原有容器独立维护 |
| API | FastAPI；客户入口与管理入口分离 | 复用现有技术；客户不能直接操作 GPUStack 管理 API |
| 持久层 | PostgreSQL 作为任务、租约、节点意图、目录和用量事件的权威记录 | 首版以数据库持久队列避免增加 Redis/RabbitMQ；用事务控制竞态 |
| 队列 | 数据库任务表，短事务领取、租约与心跳、过期回收 | 不在持有数据库锁时推理；后续吞吐确有需要再增加 broker/outbox |
| 结果 | 001 持久卷保存权威输入/结果，worker 保留临时缓存 | 完成任务不能只依赖随时退出的 worker；不再把 worker 本地文件视为可靠结果 |
| 文件接口 | 统一 ArtifactStore 接口；首版控制端文件系统，后续可替换 S3 兼容存储 | 元数据与路径分离，客户只获得有鉴权的 artifact ID |
| GPU 部署 | GPUStack 继续管理 GPU worker、模型实例、副本、显存申报及启停 | 框架不实现 GPU 放置算法，显存申报仍非硬隔离 |
| 请求执行 | 执行器支持 GPUStack 网关调用与内部实例定向调用两类 connector | 网关便于兼容；严格节点排空需能选择合格实例，见第 6 节 |
| 安全与计量 | 本地 API key 摘要、项目配额、按成功任务及实际尝试记录事件 | GPUStack 管理凭据仅在内部使用；OCR 不伪造 token 用量 |

结果方案是相对原规划的明确调整：worker 可继续缓存，但异步任务标记成功前必须把结果提交到控制端持久卷。该调整增加 001 磁盘需求；若仍要求只存 worker，应接受节点离线后结果暂不可用及磁盘损坏丢失的限制，不能宣称结果持久可查。首版按前一种方案设计。

## 3. 组件与职责

组件架构图见 [ARCHITECTURE.mmd](ARCHITECTURE.mmd) 和 [ROADMAP.html](ROADMAP.html)。

| 组件 | 职责 | 不承担的职责 |
|---|---|---|
| 外部网关 | HTTPS、请求体限制、连接上限、管理入口隔离 | 不保管任务状态、不无限重试推理请求 |
| API 服务 | API key、项目、模型目录、入参校验、幂等、任务/结果查询、同步/流式转发 | 不做 GPU 放置、不在内存里保存唯一任务记录 |
| 模型目录 | 稳定别名、不可变模型 revision、schema、模式、connector、部署绑定、资源档案 | 不把全部能力伪装为 chat/LLM |
| 执行器 | 领取任务、资格过滤、槽位预留、调用适配器、心跳、提交结果/用量 | 不直接调 Docker 或分配 GPU |
| 协调器 | 任务租约回收、重试调度、节点排空状态、实例快照刷新、过期清理 | 不把单次心跳丢失直接视为用户开机事件 |
| GPUStack connector | 隔离当前安装版本的目录/实例发现与推理调用细节 | 不假定上游有未经验证的 drain 或零损路由 API |
| 模型适配器 | 健康、能力描述、输入转换、执行、结果标准化、可选取消/进度 | 不持有客户鉴权/账本、不自行无限重试 |
| PostgreSQL | 项目、目录、任务、尝试、租约、幂等、用量及审计记录 | 不直接存大图片/视频字节 |
| ArtifactStore | 输入/输出持久保存、鉴权读取、摘要、保留期限及回收 | 不允许任意路径或任意 URL 抓取 |
| 节点 agent（后续） | 主动上报状态与请求退出，协调 Ubuntu 关机前排空 | 首版不控制 Windows 登录，不接入 MQTT/计费软件 |
| 观测组件 | 结构化日志、指标、告警、请求/任务关联 | 不记录密钥、原始图片或完整敏感推理内容 |

## 4. 模型接入协议

稳定标识如 `ocr.default` 绑定具体 revision。创建任务时固定 revision，不能因别名更新而让排队任务静默换版本。部署绑定/connector 与模型 revision 分离。

能力描述至少包含：`model_id`、`revision`、`task_type`、`input_schema`、`output_schema`、`execution_modes`（sync/async/stream）、`max_input_bytes`、`max_runtime_s`、`supports_cancel`、`supports_progress`、计量单位及资源档案。不同模型允许不同 payload schema。

内部适配器概念接口：

```text
health() -> readiness + version
capabilities() -> versioned capability document
validate(payload, artifacts) -> validated request
invoke(request, context) -> normalized result
stream(request, context) -> events                 # 可选
cancel(attempt_id) -> acknowledged/unsupported     # 可选
```

context 包含 task/attempt/request ID、截止时间、租约 fencing token、受限 artifact 读写授权；不传客户 API key 或 GPUStack 管理 key 给模型。调用鉴权的具体方式需在 connector 集成测试中验证，首版实例地址仅在私有内网开放。

标准结果：`outputs`（结构化 JSON）、`artifacts`（ID、类型、大小、摘要）、`model_revision`、实际可用的 `usage`、`warnings`。算法字段放在 outputs 内；缺乏真实 token 的模型使用图片、任务、输出数量等单位。统一错误包含 `code`、`message`、`retryable`、`request_id`，隐藏内部凭据和堆栈。

首版适配器 SDK 保持轻量，避免导入任何模型库。现有 OCR 包装为一个实现，模拟后端为另一个实现。两者共用任务核心，不能按模型类型写核心分支。

## 5. 客户接口与数据模型

| 接口（框架新接口） | 用途 |
|---|---|
| `GET /v1/models`、`GET /v1/models/{id}` | 可用模型、revision 和能力/schema |
| `POST /v1/artifacts` | 有大小/类型限制的输入上传，返回 artifact ID |
| `POST /v1/tasks` | 异步提交，支持 `Idempotency-Key`；事务提交后返回 202 |
| `GET /v1/tasks/{id}` | 查询状态、错误、输出和结果过期时间 |
| `POST /v1/tasks/{id}/cancel` | 尝试取消；不伪称模型一定停止 |
| `GET /v1/artifacts/{id}` | 所属项目鉴权后读取结果 |
| `POST /v1/inferences` | 可选同步模式：有截止时间，能力不支持则拒绝 |
| `POST /v1/chat/completions` | 按能力提供现有 OpenAI 兼容模式，包括 SSE；独立于持久任务保证 |
| `POST /admin/nodes/{id}/drain`、`resume` | 管理节点接单资格与排空，客户不能调用 |

框架新 `/v1/models` 是能力目录；不等同于现有 GPUStack 同名 API。外部入口按明确路由表转发，避免路径遮蔽；原 GPUStack Playground 继续走原私有管理入口。同步/流式连接中断首版不做自动重放，避免重复输出和重复计算；其在途执行仍计入节点活动占用。

异步请求示例（概念，不表示现已可调用）：

```json
{
  "model": "ocr.default",
  "input": {"image": {"artifact_id": "art_input_123"}},
  "parameters": {},
  "timeout_seconds": 60
}
```

核心表：`projects`、`api_keys`、`model_revisions`、`model_aliases`、`deployment_bindings`、`nodes`、`instances`、`tasks`、`task_attempts`、`execution_leases`、`artifacts`、`usage_events`、`audit_events`。所有业务记录带 project ID；任务/结果查询不允许跨项目。任务状态与终态用量事件在同一事务中写入；唯一键防止重复记成功用量。

## 6. 请求路径、排空与资源边界

异步流程：鉴权/配额 → 校验输入与 revision → 持久任务事务 → 执行器领取租约 → 选取 READY 且允许接单的实例 → 预留实例并发槽 → 适配器执行 → 临时文件写入并原子提交 → 验证摘要和文件存在 → 事务提交成功状态及用量 → 客户查询。

首版大输入通过控制端上传和受限下载，执行器不在请求中复制无限大小 base64；旧 OCR connector 可按其输入上限转换为 base64，后续模型可消费 artifact。JSON 结果同样进入持久存储策略。

**排空不等于停止心跳。** 数据库先将节点改为 DRAINING，领取新任务和槽位预留时事务检查该状态；等待其所有执行租约和实际后端执行结束，再标记 DRAINED。超时按策略等待或强制中断并记录失败/重试，不能宣称无损退出。取消或请求超时但后端尚未停止时，继续占用槽位或隔离实例，不能立即把它当作空闲。

当前 GPUStack 网关是否支持严格按节点排除必须实测，不能仅改框架状态后继续无条件调用其通用路由。设计采用以下实现顺序：

1. connector 读取已运行实例的 worker、端口和健康信息；当前试点记录已经使用过私有实例 HTTP 调用。
2. 对需排空保障的框架任务，执行器从允许接单的实例里选目标，直接调用其适配器；模型部署和 GPU 放置仍由 GPUStack 完成。地址只来自可信部署目录，不接受客户指定地址。
3. 实例重建导致快照过期时，将该尝试失败归类、刷新目录，再按有限重试策略处理。实例/节点状态与租约、槽位需事务校验；不能保证消除网络分区造成的重复计算。
4. 网关 connector 仅用于不承诺精确排空的兼容路径，或以后验证上游确有相应能力后使用。绕过框架直接从旧 Playground 发起的请求不受框架排空管理；切换系统前须关闭旁路接入并验证后端实际无任务。

资源控制分两层：GPUStack 决定实例放到哪张 GPU；框架控制任务发往哪个合格实例、并发槽位和队列长度。一个实例的初始并发上限由资源档案配置，不假定支持跨图 batching。每项目及全局均有排队上限和最大排队时间；过载返回 429/503 及明确错误。不同模型类型可配置独立任务队列，首版用 FIFO 加项目并发配额，公平调度后续迭代。

节点状态：`UNKNOWN → READY → DRAINING → DRAINED → OFFLINE`；主动 resume 后经健康确认再 READY。失联可由任意活动状态转 OFFLINE；恢复不自动撤销管理员设置的 drain 意图。001 的控制进程不属于可排空关机范围，可单独停用它的 worker，但整机仍须在线。

## 7. 任务状态与故障语义

```text
QUEUED → RUNNING → SUCCEEDED
           ├→ RETRY_WAIT → QUEUED
           ├→ FAILED
           └→ CANCEL_REQUESTED → CANCELLED / SUCCEEDED / FAILED
QUEUED / RETRY_WAIT → CANCELLED
```

- 首版执行语义为至少一次尝试，终态以 fencing token 和条件更新避免旧尝试覆盖新结果；不保证 GPU 计算恰好一次。
- 队列领取使用事务与 `FOR UPDATE SKIP LOCKED`；租约包含到期时间和递增尝试 token。协调器回收过期租约，拒绝旧 attempt 的提交。
- 幂等键作用域为项目+提交接口，绑定标准化请求摘要和固定 revision；相同键不同内容返回 409。同一任务重复执行不重复记成功计量。
- 仅重试节点失联、可恢复网络错误和明确可重试错误，指数退避加抖动、最大次数和任务总截止时间。入参错误、权限错误不重试；OOM 先隔离/降容量，不能无限原样重试。
- 取消是 best effort。排队任务可事务取消；运行任务设取消请求。模型不支持中断时允许其结束，但按确定的提交竞态规则只提交一个终态，客户端读取最终状态。
- 结果先写临时路径，再原子重命名并提交数据库；崩溃产生的孤儿文件由延迟清理回收。输入/结果清理必须排除仍被活动任务引用的数据；结果到期不重写成功历史，只返回 `result_expired`/410。
- 001 重启后从 PostgreSQL 恢复任务、租约和清理任务；数据库及文件持久卷备份并实际演练恢复。控制端不可达期间暂停接单，worker 结果提交失败必须有超时/重试上限。

建议初始可配置值：任务租约 30 秒、执行器心跳 5 秒、最多 3 次尝试、输入临时保留 24 小时、结果保留 24 小时。以上为待验证默认值，不是已承诺 SLA；模型最大时长与任务截止时间需按能力单独配置。租约持续续期，不要求长任务在 30 秒内完成。

## 8. 安全、观测与计量

网关只发布客户 API；管理操作通过私有访问。API key 只存摘要并支持撤销，模型管理凭据由内部 secret 文件注入。限制文件大小、类型、解码像素和输出大小，禁止任意远程 URL 下载与路径穿越。任务、artifact 和幂等键均按项目隔离；下载不可仅依赖难猜 ID。日志脱敏，不收集原图内容。

统一关联 request/task/attempt/project/model_revision/node/instance ID。指标包括接单/拒绝数、队列长度和最老年龄、成功/失败/重试、P50/P95、执行耗时、运行槽位、GPU利用率/显存、artifact存储量、节点失联与排空耗时。数据库/磁盘不足时停止接收新任务，而不是返回虚假的202。

用量事件区分客户成功任务、模型实际用量和内部尝试/资源消耗；计量不等于计费。失败重试成本单独保留，后续定价可核算。没有真实 token 就不填造 token 字段。

## 9. 验收原则与演进边界

核心验收不依赖某 OCR 准确率：新增模拟后端不改任务核心；重启不丢已接收任务记录；实例故障可回收租约；过期提交被拒；重复提交不重复记量；节点 drain 后不接受框架新执行；结果在 worker 离线后仍可读；项目隔离及过载行为可验证。

计划与验收用例见 [ROADMAP.md](ROADMAP.md)。阶段门槛通过后再发布外部客户入口。首版只有单控制端重启恢复，非高可用，也不保证双系统切换零失败。更多 worker、消息中间件、对象存储、独立控制端与多模型调度按实际瓶颈渐进增加。
