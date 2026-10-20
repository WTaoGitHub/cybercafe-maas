# 统一模型服务框架设计 v0.2

日期：2026-10-20 · 状态：当前设计基线；P0已验收、P1模拟闭环已验证，P2–P4待实现。
固定依赖：GPUStack **v2.2.3**；不依赖main、不修改上游数据库、不以fork上游为实施前提。
配套：[实施计划](PLAN.md)、[路线图](ROADMAP.md)、[组件架构](ARCHITECTURE.mmd)、[HTML总览](ROADMAP.html)。

## 1. 目标与范围

利用可能退出的闲置GPU，通过一个模型服务底座提供同步、流式及持久异步调用。模型算法不是框架核心：先用既有OCR与模拟后端验证接入、持久性、故障恢复和结果可查。

本版落实最新讨论：**充分复用GPUStack v2.2.3，但保留最小任务数据库、执行器与结果管理。** 不将“版本固定”等同于“只能提供同步请求”，也不重复建设模型网关、用户中心或GPU调度器。

首期必须具备：已接受任务可追踪、有限重试、幂等、结果持久查询、项目隔离、重启恢复，以及有明确限制的计划维护。首期不承诺：任务恰好执行一次、无损实时迁移、任意worker随时切Windows、控制端高可用、计费结算、任意视频后端。

## 2. 版本、证据与方案沿革

源码基线为本地v2.2.3 commit `1cfcbbff02aacdb51f0a390378dd7bcddd9bf365`。上线前核对实际容器digest、依赖和网关模式，不能仅凭标签断言与本地源码一致。源码审查与证据见 [复用分析](GPUSTACK-REUSE-REVIEW.md) 和 [证据JSON](GPUSTACK-SOURCE-EVIDENCE.json)。

本版替代v0.1宽泛自建方案及“暂缓任务数据库/执行器/结果管理”的收敛建议。v0.1原文归档于archive/；GPUSTACK-V2.2.3-SCOPE仅保留为已被替代的讨论记录。根目录MILESTONES.md继续维持deprecated。本版ROADMAP为当前执行路线，不并行执行旧F0–F6或V1–V4。

明确排除main特有依赖：新LB/least-load/session-affinity插件、model revision回退API、角色组drain。v2.2.3 maintenance不是业务排空；模型部署恢复不是客户任务恢复；内部WorkQueue不是持久推理任务队列。

## 3. 最小组件与职责

| 组件 | 归属 | 本版职责 |
|---|---|---|
| 统一边缘入口 | 复用现有/标准网关 | HTTPS、体积/连接限制、路径分流；不另写通用推理代理 |
| GPUStack v2.2.3 | 复用 | 用户/组织、API key、模型ACL、目录/别名、同步/SSE/可验证的generic proxy、部署、副本、GPU放置、worker健康、基础用量/指标 |
| 任务API（FastAPI） | 新增，薄层 | 上传/查询artifact、提交/查询/取消任务、幂等、业务授权、结果下载、维护管理 |
| PostgreSQL | 新增，独立业务数据库 | 任务队列、attempt/租约、业务项目映射、用量事件与artifact元数据；不复制GPUStack部署状态 |
| 执行器（同一代码库） | 新增，一个进程角色 | 领取任务、续租、调用GPUStack、收集结果、条件提交、有限重试 |
| 协调循环 | 执行器内模块 | 过期租约回收、延迟重试、过期文件清理、全局维护协调；首期不拆独立服务 |
| ArtifactStore | 任务层内模块 | 控制端文件持久卷保存输入/权威结果、摘要、鉴权读写及TTL；首期不部署独立结果微服务 |
| 模型适配器 | 按模型新增/复用 | 模型输入输出、健康、错误、实际执行；只补GPUStack未标准化的业务协议 |
| 观测 | 复用+补充 | 复用GPU/请求指标；新增队列、租约、任务、结果、维护与成功计量指标 |

新增运行单元只有任务API、执行器、PostgreSQL；artifact是持久卷而非额外服务。一个仓库，Docker Compose管理新增单元。001控制端常在线且兼worker，002提供可维护worker；不改变现有GPUStack自己的数据库配置。

## 4. 两条调用链与权威来源

### 4.1 同步 / 流式

客户 → 边缘网关 → GPUStack推理入口 → 模型适配器 → 客户。

继续使用现有模型名、API key和SSE；这类调用不具备后台任务持久性，连接中断不自动重放。Playground走原私有管理入口。普通原生HTTP接口先验证v2.2.3 generic proxy，再决定是否启用；不强行把所有模型包装成chat。

### 4.2 异步

客户上传输入 → 任务API鉴权/检查模型权限 → PostgreSQL事务提交任务并返回202 → 执行器领取租约 → 通过GPUStack入口调用具体版本目标 → 控制端持久写入结果 → 同一事务提交任务成功和唯一成功用量事件 → 客户查询/下载。

**任务受理202代表任务记录已持久化，不代表推理已完成。任务成功代表结果已提交控制端，不以worker本地文件或HTTP200单独作为成功依据。**

| 数据 | 权威来源 | 任务层保留范围 |
|---|---|---|
| 用户、key、组织、模型权限 | GPUStack | 引用和受控映射，不另造账号密码 |
| 模型别名、部署、实例、worker状态 | GPUStack | ID及必要只读快照；不反向覆盖权威状态 |
| 业务项目、幂等、任务、尝试、保留期 | 任务PostgreSQL | 任务层权威记录 |
| 输入与已成功结果 | 001持久卷+artifact元数据 | 权威持久结果，worker仅可缓存 |
| 调用次数/token/资源基础用量 | GPUStack | 保留来源；不等同于客户成功任务数 |
| 成功任务唯一计量与失败尝试 | 任务PostgreSQL | 为以后计费提供证据，首期不出账单 |

## 5. 客户身份与任务权限：复用key，不假设现成SSO

v2.2.3没有已验证可直接用于任意新任务端点的通用identity接口。`/token-auth`是模型级ext-auth，依赖模型上下文；management接口不应要求客户提供management key。

**首期采用受控接入的凭据绑定**，不开放任意GPUStack key自动注册任务服务：

1. 管理员通过GPUStack签发/选择非管理员、inference-only、限定模型的客户key，并登记其key ID、owner/组织、业务project映射及允许的版本目标；映射只能由可信管理流程建立。
2. 客户仍使用这一个GPUStack key调用任务API。任务层用本地HMAC指纹识别已登记凭据，再调用GPUStack验证有效性；不保存第二份密码，不凭客户提交的身份头认定project。
3. 每次受理任务，按目标私有/受限模型通过GPUStack原生鉴权路径检查该key的权限。候选路径为私有`/token-auth`；必须验证其scope、有效期、撤销与缓存行为。普通`/v1/models`返回模型列表不能单独当作全部模型白名单权限证明。
4. 查询/下载也验证key有效性并核对记录所属project；不把“知道task/artifact ID”当作授权。模型权限撤销后是否允许读既有结果，首期规则为key仍有效且project授权可读；不因别名改动重算历史归属。
5. 执行器仅持有credential引用；注册时需要的推理key通过控制端受保护secret存储（加密、解密密钥独立挂载、最小权限）取得。提交前/每次执行前重新验证对应key和模型权限；key撤销或到期时不借全局管理key越权执行，未执行任务明确失败为AUTH_REVOKED/AUTH_EXPIRED。
6. 管理key只供受保护的登记/目录查询管理过程使用，不进入任务payload、模型容器、浏览器或日志。解析/缓存上游身份结论封装在AuthConnector中，不导入GPUStack内部auth模块或直读它的数据库。

此桥接已按受控登记方式实现，并通过真实两组织key及task/artifact隔离验证；不支持任意上游key自动注册。若上述HTTP验证在实际v2.2.3配置下无法可靠判定有效性和归属，先限定单个受控试点project并停止外部多租户开放；不得悄悄退化为只比对本地key指纹。未来是否引入通用身份服务另行设计。

## 6. 模型接入与版本固定

GPUStack提供backend镜像、启动命令、环境、健康、模型route和副本配置。任务层只加一份轻量能力清单，按GPUStack route/model ID关联：输入/输出schema、sync/stream/async模式、输入限制、最大执行时长、取消能力、结果转换及实际计量单位。

适配器概念接口：`health`、`validate`、`invoke`、可选`stream/cancel`。统一的是任务生命周期，不是所有模型payload。健康检查与部署协议沿用GPUStack；输入解析与输出规范由适配器负责。模拟后端不依赖GPU，用来注入延迟、失败和连接中断。

v2.2.3没有main的部署revision API。使用Git能力清单+固定镜像digest/权重摘要+配置快照；任务受理时记录不可变业务revision与具体版本化route。示例：客户别名`ocr.default`解析到登记的版本目标`ocr-r03`。活跃任务引用的版本目标禁止原地改绑；更新采用新目标，旧目标按引用与保留策略退役。执行前核对配置指纹；不匹配明确失败MODEL_REVISION_CHANGED，不静默切到新版本。

首期保持GPUStack为路由权威，任务层的版本记录是受理快照，不是第二个可变别名控制器。为固定历史版本创建额外部署可能消耗显存，因此不承诺所有旧版本永久在线；资源不足时可排队或按显式错误结束，不能冒充旧版本运行新模型。

## 7. 任务与数据协议

新任务端点使用`/maas/v1`命名空间，与GPUStack `/v1/models`等既有接口分开：

| 方法/路径 | 行为 |
|---|---|
| `POST /maas/v1/artifacts` | 限制大小/类型的上传；返回artifact ID及保留期 |
| `POST /maas/v1/tasks` | Idempotency-Key；事务成功后202，含task ID与状态地址 |
| `GET /maas/v1/tasks/{id}` | 状态、revision、错误、outputs、结果有效期 |
| `POST /maas/v1/tasks/{id}/cancel` | best-effort取消；不保证模型中断 |
| `GET /maas/v1/artifacts/{id}` | project鉴权下载；结果到期410 |
| `GET /maas/v1/models/{id}/capabilities` | 补充schema，引用GPUStack模型目录，不重复实现目录 |
| `POST /maas/admin/maintenance`、`resume` | 私有管理入口，全局受控维护流程 |

提交示例：

```json
{"model":"ocr.default","input":{"image":{"artifact_id":"art_123"}},"parameters":{},"timeout_seconds":120}
```

错误响应包含code、message、retryable和request_id，不泄露内部地址、secret或堆栈。模型JSON200也需执行输出schema检查；模型特有错误应明确分类。

核心表：projects（绑定）、credential_bindings（引用/指纹）、model_capabilities（版本清单）、tasks、task_attempts、artifacts、task_usage_events、maintenance_state、audit_events。tasks包含project、提交摘要、幂等键、credential_ref、模型revision/具体目标、截止、状态、attempt fencing token和结果引用。使用唯一约束保证project+幂等键及task成功事件唯一。

任务状态：`QUEUED → RUNNING → SUCCEEDED`；失败可进入`RETRY_WAIT → QUEUED`或FAILED；排队/重试等待可取消；RUNNING可进入CANCEL_REQUESTED，最后确定CANCELLED/SUCCEEDED/FAILED之一。终态条件提交裁决竞态，不承诺取消一定胜过已完成结果。

## 8. 执行、恢复和重试边界

PostgreSQL同时作为任务记录与持久队列。执行器短事务`FOR UPDATE SKIP LOCKED`领取任务、递增attempt token并设置租约，事务外执行；周期心跳续期，协调循环回收过期租约。结果提交使用token条件更新，拒绝旧attempt覆盖新结果。

初始配置建议：租约30秒、心跳5秒、最多3次业务attempt、单project及全局有界队列、可配置并发槽、最长排队时间。长任务持续续租，不要求30秒内结束。超时和退避均受任务总deadline约束；参数需P1验证，不是外部SLA。

只重试明确的可恢复网络/节点故障；入参、权限、配置版本变化不重试；OOM标记容量问题，不无限原样重试。一次客户任务可有多次实际GPU执行，终态与成功计量只能提交一次。

v2.2.3 Higress配置可能包含`non_idempotent`及上游2次尝试，Python fallback与Higress不同。P0必须核对实际配置、fallback和模型内部重试。对于任务专用版本目标，优先通过可用配置禁用透明上游重试；如果无法关闭，应记录每attempt可能触发的上游尝试上界、降低业务重试预算。结果提交幂等不等于GPU计算恰好一次，不把未知实际执行次数报告成精确资源账单。

首期执行器通过GPUStack入口调用，不默认直连实例，因此保留其权限/LB/统计链路；request/task/attempt ID通过经过验证可传递的头和本地日志关联，上游不支持字段不伪称能完整串联。

超时/失联后的实际模型执行可能未知。保留UNKNOWN_BACKEND_ACTIVITY标记并保守占用/暂停相应任务执行域；未证明后端停止，不把HTTP断开当作安全关机依据。隔离/核实后才释放容量或恢复派发，避免重试导致无限后台叠加。

## 9. 结果持久性与清理

001持久卷保存输入及权威结果；数据库只存元数据与小型结构化摘要，不存大文件。worker返回结果后由执行器落盘；对于返回worker本地URL的后端，必须先通过受控connector拉取到001才能标记成功。结果过大、类型不符或拉取失败时不伪报成功。

先写临时文件、计算摘要、flush/fsync及原子rename，再事务提交结果引用、成功状态和唯一计量事件。崩溃产生的孤儿文件由延迟清理；如果数据库事务失败，旧attempt不得借重试覆盖已提交的新结果。存储检查失败或磁盘预算不足时停止接受新输入/任务。

上传输入默认未引用TTL24小时；被非终态任务引用时不删。成功结果默认24小时，可配置；过期后保留历史任务和用量，下载返回410。失败任务输入按单独保留策略清理。清理需要事务/锁或标记流程避免与新引用竞态。

001数据库与artifact卷均需备份及恢复演练；同机持久卷解决worker退出及进程重启，不解决001硬盘损坏或控制端高可用。备份RPO/RTO实测后说明，不能宣称零丢失。

## 10. 节点维护：首期采用全局受控排空，不伪造逐节点能力

v2.2.3 maintenance阻止新部署，不保证已有实例停止接推理；经通用入口派发时任务层也不能准确知道请求实际落到哪个worker。因此本版**不实现第二套实例级调度，也不承诺逐节点精确无损drain**。

首期流程：

1. 持久记录全局MAINTENANCE_PENDING，暂停执行器领取/派发；未执行任务继续保留。任务接收可按队列容量继续或返回明确维护错误。
2. 边缘网关暂时拒绝新的同步/流式推理，原私有Playground也需停止旁路调用；仅暂停任务层不算完整排空。
3. 等待所有受控请求和任务attempt结束，核对超时/失联造成的未知后端执行；无法证明结束时禁止自动确认安全关机，转人工处理。
4. 设置目标worker maintenance，完成计划停机/切系统。恢复业务前确认仍有合格实例；离线旧RUNNING实例已退出路由、目录缓存/网关配置已收敛，并做真实调用探测。不能因maintenance PUT成功就恢复派发。
5. 解除全局暂停，记录维护过程与失败任务。002恢复Ubuntu后按健康和人工resume流程重新纳入。001整机保持在线，可维护worker但不能随意关闭控制服务。

这会短暂暂停其他节点的业务，换取不绕过GPUStack的简单首版。若未来要求只排空002且001不中断，再评估独立路由分组、受控实例connector或上游扩展；必须同时处理权限、usage、旁路请求和传播竞态。第二阶段另行设计，不偷偷纳入本期保证。

## 11. 安全、配额与观测

对外网关仅开放已验证推理路径和`/maas/v1`，管理路由私有；任务端点不信任外部身份头；artifact使用随机ID且强制project授权。禁止任意URL抓取与文件路径输入，限制上传/解码/输出/存储；日志不记录key、原图或完整敏感内容。

任务层设项目并发、全局并发、排队数量/年龄/磁盘配额；GPUStack负责GPU放置，任务槽不是显存硬隔离。初期FIFO+项目并发限制，多租户公平性以测试为准，不宣称加权公平调度已实现。

复用GPUStack资源、路由和用量看板；新增任务成功/失败/重试、队列最老年龄、过期租约、未知后端活动、存储量、维护时长、任务到端延迟和唯一成功事件。OCR不伪造token；内部GPUStack调用次数与客户成功任务数分开记录。客户key直调同步请求和任务服务内部调用会同时出现在基础usage中，不重复相加当作客户订单数。

## 12. 部署、验收与演进

上线采用新命名空间/端点，先不替换原rapidocr-gpu路由。新增组件独立配置和持久卷，部署前备份；原同步服务保留回退。镜像digest、依赖、迁移和secret模板入Git，实际secret不入Git。

首期验收：同key身份复用、两个project隔离；两个后端接入不改任务核心；202后重启任务可恢复；旧attempt提交被拒；结果在002离线后可读；排空过程不把maintenance误当作已完成；8小时运行无无法解释的任务遗失或重复成功计量。

后续才考虑精确逐节点drain、自动双系统agent、S3对象存储、独立消息队列、独立常在线控制端、商业账本与绘图/视频。固定v2.2.3不阻止任务层独立演进；升级GPUStack另做隔离回归，不绑定首期关键路径。
