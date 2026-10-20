# GPUStack 源码复用分析：main 与 v2.2.3

分析日期：2026-10-20（Asia/Shanghai）
对照设计：[DESIGN.md](DESIGN.md)、[ROADMAP.md](ROADMAP.md)
证据：[源码定位与片段](GPUSTACK-SOURCE-EVIDENCE.json)

## 1. 结论

当前设计把 GPUStack 的职责划得过窄，重复规划了模型目录/别名、API key、组织与权限、同步/流式推理入口、基础用量与指标。应把 GPUStack 视为既有模型服务底座，而不只是 GPU 调度器。

建议保留“轻量 MaaS 业务扩展层”：持久异步任务、输入/结果 artifact、任务幂等与重试、网吧节点排空工作流、业务能力 schema、任务级计量。通过 connector 使用 GPUStack 已有能力，不直接读写其数据库、不复制用户/节点/部署表的权威状态。

**不建议为复用而直接升级 main。** 当前 v2.2.3 已足够承接大部分基础能力；main 作为后续能力参考与隔离验证对象。普通 OCR 的精确排空不能因看到 `maintenance` 或 `draining_since` 就判定已解决。

## 2. 检查范围与可信度

- 本地仓库：`/Users/4paradigm/dev/gpustack`；检查时工作区干净，当前分支 main。
- 本地 main：`c18f7fabc6e0fc5c7d0237bb799247ab1a02de3b`，提交时间2026-10-19 21:52:03 +08:00。
- 本地 v2.2.3：`1cfcbbff02aacdb51f0a390378dd7bcddd9bf365`，提交时间2026-07-28 10:22:42 +08:00。
- 未 fetch、checkout、修改 GPUStack 仓库或部署；main 不表示已确认的远端最新状态。所有版本结论仅针对上述本地 commit，不按文件时间或当前main内容倒推tag功能。
- 静态追踪了 schemas、路由注册、auth、gateway、实例选择、worker controller、队列、文件、usage与metrics；未运行上游测试，也未检查远程容器是否与该本地tag逐字相同。
- v2.2.3 使用 `git show v2.2.3:<path>` 独立读取；main 使用对应固定commit。下文源码位置均可通过上述 JSON 的版本化证据核对。
- “可复用”指源码存在对应实现，不等于当前配置已启用、依赖已安装或已经达到业务SLA。“未找到”限于已检查的注册路由和实现范围，不是对所有外部后端的否定。

## 3. 组件重叠与复用清单

| 我们设计的组件/能力 | v2.2.3 源码实际能力 | main 的相关变化 | 复用结论与保留范围 |
|---|---|---|---|
| GPU调度、模型部署、副本、资源申报 | 已有worker过滤、实例controller、自定义backend和副本维护 | 增强角色组、部署配置与相关生命周期 | **直接复用**，不写第二套GPU调度/容器生命周期 |
| 自定义模型接入、健康检查、镜像版本 | `InferenceBackend`/`VersionConfig`含image、entrypoint、run_command、env、health_check_path | 更丰富来源与部署配置 | **复用部署规范**；另补业务schema、artifact、取消/进度协议，不重复造部署SDK |
| 模型别名、目录、路由、访问可见性 | `ModelRoute`/targets、权重、模型名覆盖、meta、访问策略；`/v1/models` | 插件化路由、更多策略 | **直接复用**别名/ACL/部署绑定；能力schema作为薄扩展，别再维护一份别名权威表 |
| 同步推理及SSE入口 | OpenAI兼容路由、JSON/multipart解析、流式worker转发、超时错误 | 改进流式错误；Python fallback仍用轮询策略 | **优先复用**；客户同步/流式请求不再默认绕到自建执行器 |
| 非LLM原生HTTP服务入口 | `generic_proxy`及`/model/proxy/<route_id>/<path>`网关路径重写 | 保留并接入路由插件体系 | **优先验证复用**，普通OCR不必因平台限制永久伪装为chat；不因此移除现有Playground兼容 |
| API key、撤销/过期、模型白名单、scope | key摘要、有效期、inference/management范围、allowed_model_names | 新增校验digest和删除缓存失效等实现 | **复用身份源和key管理**；新任务/结果API需安全的身份桥接，不能自动假定现有推理scope能访问新接口 |
| 用户、组织、组、模型权限 | principals、tenant context、组织成员和route grants在本地tag中已存在 | 进一步扩展和修复隔离相关路径 | **复用组织/权限**；项目仅保存确有需要的业务映射，不克隆账号体系；任务/artifact仍须自己做对象级授权 |
| worker心跳、资源、在线状态、标签 | worker状态、同步、不可达标记、离线实例清理与重调度 | 状态缓冲、清理与组处理增强 | **直接复用观测事实**；框架只记录网吧业务接单意图及任务租约 |
| 维护/排空、上线下线 | `maintenance`会使worker进入MAINTENANCE并排除新部署，但不等于停止已有实例推理 | 角色组有`draining_since`及软缩容窗口 | **部分复用**maintenance作为部署侧禁入；业务排空、在途确认、系统切换仍需补齐 |
| 请求级负载均衡、故障fallback | Python入口RoundRobin；Higress路线有权重及fallback配置 | LB、least-load、session-affinity、decision-service插件；max_running_requests | **复用常规LB/fallback**，不重做；main插件不是持久任务队列，也不是精确事务槽位 |
| 限流、配额、有界排队 | 能看到网关插件基础和consumer身份，但未找到完整的项目任务配额/持久排队实现 | 并发过滤与负载策略增强 | **任务配额/有界队列自建**；网关限流可用插件时配置复用，不因注释含rate-limit就认定已完成 |
| 重试、幂等、租约、fencing | controller WorkQueue有去重/退避，但为内存控制循环；HTTP代理有超时/fallback | WorkQueue等机制继续存在 | **业务任务层自建**；不能拿reconcile事件队列替代已接受推理任务的持久记录 |
| 输入上传、结果查询、TTL、artifact | model_files/ModelFileManager管理权重；worker filesystem是运维文件接口 | cache服务、持久卷等基础设施更丰富 | **业务ArtifactStore自建**，可复用基础存储/缓存能力；权重缓存不是客户结果库 |
| 请求/token/资源用量 | ModelUsage、明细、gateway上报、归档及资源metering | 明细加入request_id等，增强关联 | **复用底层用量**；业务成功任务去重、图片/任务单位、失败尝试成本需补充 |
| 指标、日志、监控面板 | worker资源采集、metrics exporter、Prometheus/Grafana入口、gateway指标 | 更丰富的路由与运行指标 | **复用已有监控**，只加任务队列、租约、排空、artifact、客户业务指标及告警 |
| 部署revision与回退 | backend版本和模型配置可保存；本地tag无`routes/model_revisions.py` | 新增model revisions、rollback-preview、rollback API | **v2先保留现有快照回退**；main可评估复用，不重复实现其部署revision库；任务仍需固定业务版本 |
| 数据库、迁移 | 既有GPUStack数据库/迁移维护其控制状态 | 更多配置与状态表 | **复用其控制面，不扩写其内部表**；异步任务使用独立schema/数据库及迁移 |

## 4. 几项最重要的源码结论

### 4.1 模型目录、同步接口、原生协议入口已经存在

v2.2.3 `gpustack/routes/openai.py:90`附近的 `list_models`读取可见路由；`proxy_request_by_model`解析模型和stream，验证模型权限、选择路由target和实例，再经worker代理调用。`gpustack/gateway/utils.py:123`起列出chat、responses、embeddings、图片、音频等路由。接口存在只表示转发能力，具体后端仍需实现。

`gpustack/gateway/__init__.py:539`的`generic_proxy_router_plugin`与`gateway/utils.py:643`附近的规则支持`/model/proxy/<route_id>/<path>`。本地tag的`schemas/models.py:259`已有`generic_proxy`字段。

**改设计**：既有 `/v1/chat/completions`、模型别名和同步/流式路由留给GPUStack。原生 `/ocr` 等新接入先验证generic proxy的鉴权、路径、请求体、响应和取消边界，再决定是否需要自建转发。`routes/proxy.py`的HF/ModelScope代理不是该模型通用代理，不能混用。

### 4.2 身份与组织可以复用，但新任务API不能直接“继承”权限

v2.2.3 `schemas/api_keys.py:17`起的scope、`:53`起的ApiKey包含hashed secret、组织owner和过期时间。`api/auth.py`提供身份验证及scope检查；`api/tenant.py`和route principal grants已有组织隔离。main对secret校验和缓存失效增加了实现。

新框架若在另一个服务中，不能仅凭`X-User-Id`/`X-Organization-Id`接收客户声明。也不能为了验证身份要求客户提供management key。现有`/token-auth`是模型级ext-auth，依赖模型上下文；它不是对任意task/artifact端点都适用的通用identity API。

**推荐接法**：建立一个受信任的身份桥接（在GPUStack侧扩展一个最小identity/scope验证接口，或通过经过验证的网关ext-auth和受保护的内部上下文），输出principal/key/scope；框架按该身份授权任务和artifact。身份桥接若暂不能实现，再用独立业务key作为明确的过渡方案，而非无意中维护两套正式账号体系。提交任务还须检查模型授权，后续查询已有结果的权限规则应单独写定。

### 4.3 maintenance 不是请求 drain

v2.2.3调用`PUT /v2/workers/{id}`可更新maintenance；`schemas/workers.py:379`附近`compute_state`将worker置MAINTENANCE；`policies/worker_filters/status_filter.py:21`只允许READY worker进入新的部署候选。

但Python推理入口调用`ModelInstanceService.get_running_instances`，v2.2.3 `server/services.py:905`只按model_id和RUNNING过滤，没有联查worker maintenance。`WorkerController`仅在unreachable/NOT_READY等路径标记实例不可达；maintenance未触发相同处理。`worker_instance_cleaner.py`还刻意避免对维护节点自动清理离线实例。

**因此不能把maintenance当作已有实例停止接单或任务排空证明。** 它适合计划维护、避免新放置和避免不必要重调度；需与业务drain组合使用。还需验证网关控制面传播和缓存窗口，不能只看一次PUT成功。

main `server/controllers.py:2374`的`_soft_scale_down_enabled`明确仅在`model.roles`存在且配置drain窗口时启用软缩容；无角色普通模型直接删除缩容候选。带角色组可从router registry移除成员并等待固定窗口，但固定窗口也不是所有在途推理必然结束的证明。`cancel_drain`辅助函数本身无生产触发入口，不能当成节点drain API。

**保留自建**：网吧的接单意图→停止新业务任务→核对在途→允许切系统；001控制端不得随普通worker关机。优先为GPUStack增加小的路由资格过滤/排空扩展并测试；暂做不到时，才使用原设计的定向实例connector。

### 4.4 main 的新 LB 可复用，但不保证通用任务限流和持久排队

main 的`routes/plugins/lb/`、`least_load/`、`session_affinity/`等以Higress规则/WASM插件实现，`ModelRouteTarget.max_running_requests`为可选过滤参数。相关插件代码依赖manifest中的可用模块，模块不存在会退化/不生效；main Python fallback的`http_proxy/load_balancer.py`仍默认RoundRobin。

本地tag的`gpustack-higress-plugins==0.3.0`与main的`==0.5.0`不同。该独立依赖包源代码不在本次仓库检查范围，不能仅凭Python配置断言具体WASM运行行为已验证。不能只摘拷Python LB类到v2.2.3就期望功能成立。

**复用范围**：常规请求分配、路由策略、合适的fallback。**保留范围**：项目任务队列、任务接受语义、幂等、事务租约和实际模型停止后的槽位释放。实时引擎指标过滤也不同于严格事务式并发预留，OCR需确认是否提供插件依赖的指标。

### 4.5 内部工作队列不等于推理任务平台

两个版本都有`server/workqueue.py`，开头说明其是controller reconcile的内存队列，提供per-key去重、延迟和退避。它处理部署状态收敛；不是客户提交后的持久推理任务表。

在已检查的注册路由和状态模型中，未找到覆盖我们要求的通用`/tasks`、任务幂等键、attempt租约/fencing、持久输入/结果、结果TTL和节点故障后任务恢复的完整链路。`/responses`透传、benchmark任务或后端自己的异步job也不等同于此能力。

**保持自建**：任务API、PostgreSQL持久任务队列、有限重试、attempt、结果提交及TTL。可借鉴队列设计，但不把上游内部类当作稳定公共SDK。

### 4.6 用量与监控应复用，业务成功计量仍需补充

v2.2.3已有`ModelUsage.request_count`、token计数、`ModelUsageDetails`及`/usage/gateway-metrics`上报；`metered_usage.py`明确只计量资源数量，不含价格。资源metering面向GPU/CPU实例和存储，不能直接当成普通模型实例的逐请求板卡电费。

main `ModelUsageDetails.request_id`支持更好的请求关联。自建层需补task/attempt关联、成功任务唯一事件、图片/任务计量及失败重试成本。当前OCR SSE刻意不伪造usage；不能因此假定GPUStack已完整记录每个OCR请求及其正确单位，需专门验证generic proxy和自定义JSON/SSE路径的统计覆盖。

定向私有实例调用会绕过GPUStack入口鉴权、用量中间件和网关LB。若为drain暂时采用该路径，必须由任务服务承担客户授权、调用凭据保护、任务计量和trace，并明确上游统计缺口；不能报告为“同时完整复用了入口统计”。

## 5. 建议收敛后的组件归属

| 组件 | 权威归属 | MaaS只保留什么 |
|---|---|---|
| 身份、API key、组织、模型ACL | GPUStack | 安全身份桥接、业务项目映射、任务/artifact授权 |
| 模型部署、后端、镜像版本、GPU放置、副本 | GPUStack | 部署引用、不可变业务revision记录、兼容connector |
| 别名与同步/流式模型入口 | GPUStack | 能力schema、输入适配、必要业务校验 |
| worker注册、心跳、资源和健康 | GPUStack | 接单/排空意图、在途业务占用、双系统切换协调 |
| 常规请求LB与网关fallback | GPUStack/Higress | 任务级重试；精确排空扩展或临时定向调用 |
| 持久异步任务、幂等、attempt、租约 | MaaS | 全部，独立持久数据与迁移 |
| 输入/结果artifact与TTL | MaaS | 全部，后端可使用通用对象存储 |
| 基础请求/资源指标与用量 | GPUStack | 任务指标、业务单位、唯一成功计量与关联 |
| HTTPS与外围访问限制 | 已有/标准网关优先 | `/tasks`与`/artifacts`入口路由和管理隔离，不自写通用网关 |

保留独立任务数据库，但删去重复的用户管理、节点注册、部署调度与通用同步代理。缓存表可存GPUStack ID及快照，但禁止形成与上游相互覆盖的第二权威源。

能力扩展优先利用route meta（在本地tag存在）或独立按route/model ID关联的能力表；不要在一个模型名上重复维护两套可变路由。任务受理时固定具体业务revision及部署目标，不能仅记录将来可能改绑的alias；main的部署revision不自动保证旧任务仍能执行旧模型。

## 6. 修订后的实施优先级

1. **F0先做复用探针**：generic proxy原生OCR、现有API key/组织权限、任务服务身份桥接、maintenance下的请求流向、调用链用量覆盖。形成当前v2.2.3可用能力清单。
2. **F1只建设持久任务和artifact最小闭环**：使用现有身份映射和模型路由引用；模拟后端加现有OCR，不做第二套模型网关/用户中心。
3. **F2补业务drain和故障恢复**：先验证小扩展复用GPUStack入口的可能性；严格drain无法满足时再采用受控定向connector，补齐统计与授权。
4. **F3复用既有Prometheus/Grafana/usage**：仅加业务指标、任务配额和计量，不重复采集全部GPU指标。
5. **F4复用网关和交付能力**：加任务端点路由、备份恢复、业务隔离与过期清理。main的LB/revision另开隔离验证，不绑定F1关键路径。

原18–28人日估算不能直接按“复用项数量”减去工期：身份桥接、网关策略和定向调用统计仍有集成成本。F0完成后重新估算。此文件为对v0.1设计的审查建议，尚未重写DESIGN/ROADMAP原文或修改线上部署。

## 7. 必做验证清单

- 同一inference-only key能否调用原生generic OCR；过期/删除/缩小模型权限后的缓存传播行为。
- 两个组织访问模型目录及新任务/artifact的隔离；伪造身份头是否被忽略；不使用management key替代客户认证。
- maintenance期间持续发送请求，分别观察Python fallback和实际Higress入口，确认已有RUNNING实例是否继续接单。
- drain与任务领取/请求派发竞态；取消/超时但GPU仍执行时不得提前关机。
- generic代理的JSON、multipart、SSE、错误和模型名重写；原生payload中名为model的业务字段是否被改变。
- 自定义OCR和generic请求有无usage行；内部任务重试与外部成功任务的统计是否分清；定向路径是否绕过上游统计。
- main的LB依赖版本/manifest、OCR指标适配与过载行为；不把引擎指标过滤误作持久任务配额。
- main部署revision回退与已有自定义OCR镜像兼容；备份后离线迁移验证，不直接升级线上001。

## 8. 主要源码定位

版本化片段和精确行号统一收录于 [GPUSTACK-SOURCE-EVIDENCE.json](GPUSTACK-SOURCE-EVIDENCE.json)。可在GPUStack仓库执行：

```sh
git show v2.2.3:gpustack/server/services.py
git show v2.2.3:gpustack/schemas/workers.py
git show v2.2.3:gpustack/gateway/__init__.py
git show main:gpustack/server/controllers.py
git show main:gpustack/routes/model_revisions.py
```

关键位置：`schemas/api_keys.py`、`api/auth.py`、`api/tenant.py`、`routes/model_routes.py`、`routes/openai.py`、`gateway/utils.py`、`gateway/__init__.py`、`schemas/inference_backend.py`、`server/services.py`、`schemas/workers.py`、`policies/worker_filters/status_filter.py`、`server/worker_instance_cleaner.py`、`server/controllers.py`、`server/workqueue.py`、`schemas/model_usage*.py`、`schemas/metered_usage.py`、`server/metrics_collector.py`、`routes/plugins/`和`routes/model_revisions.py`。
