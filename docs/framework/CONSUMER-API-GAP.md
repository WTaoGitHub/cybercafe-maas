# 外部消费者生成接口与当前MaaS API差异

2026-10-21。分析输入：本机 `/Users/4paradigm/Downloads/upstream-generation-api-integration.md`；实现依据为当前api/schemas/sync_ingress/ocr_connector/auth/limits、Nginx与计量SQL。仅静态核对，未取得消费者源码或开展联调。本次不修改API、部署、服务器或P4测试计划；不计入E1候选能力，忽略音频/视频接口及其轮询/长周期要求。

## 1. 结论

**OCR目前不能直接接入，应新增消费者兼容的同步OCR门面，复用当前鉴权、固定模型、同步额度/维护与UNKNOWN机制。文生图尚未实现，需要新模型驱动/运行时和计量能力，不是路径改名。**

保留现有 `/v1/chat/completions`、`/maas/v1/tasks`、artifact等接口，避免破坏已验证客户端。消费者外层对自己的用户统一返回任务，不代表它要求本系统的OCR也异步：文档4.1明确本系统OCR同步返回lines；9.2中的task_id/PENDING是消费者自身网关的外层API。

## 2. 接口差异清单

| 项目 | 消费者要求 | 当前系统 | 处理 |
|---|---|---|---|
| OCR提交路径 | POST `{endpoint}/ocr` | 对外仅Chat同步或artifact+异步任务；Nginx未允许/ocr | 新增兼容路由并同步更新HTTP/TLS路径白名单 |
| OCR输入 | model + image_url/image_base64二选一 | 同步messages中的base64 data URL；异步需artifact_id | 增加独立请求schema及输入转换；不能原样转发 |
| OCR输出 | 顶层lines，行字段text/confidence/polygon | Chat envelope内content为JSON字符串，行字段text/score/box；异步再包outputs | 解包并映射score→confidence、box→polygon，顶层直接返回lines |
| image_url | HTTPS图片地址 | OCR后端只接受data URL，不下载远程图片 | 若消费者会发URL，新增受限下载；若首期仅base64，双方明确约束，不宣称全兼容 |
| language | 可选 | 无显式语言选择或可靠输出语言检测 | 明确支持的hint/拒绝策略；不要把传入zh伪装成检测结果 |
| return_boxes | 可选；false可无坐标 | 固定返回box | 门面控制polygon输出；默认值需双方约定 |
| Bearer鉴权 | Authorization + Content-Type | 已支持，但key还须本地登记project/模型，并通过GPUStack鉴权 | 不改鉴权协议；为消费者开通受限key及逻辑模型权限 |
| model | 消费者示例qwen-vl-ocr/qwen-image | 当前正式逻辑名ocr.default | 消费者配置真实支持的模型名；如需别名，显式登记，不能冒充不同算法 |
| request_id | body可选透传，不发送幂等头 | 同步无幂等；异步必须Idempotency-Key；响应X-Request-ID由本系统新生成 | 新门面接受并关联body request_id；独立约定上游幂等，不直接复用旧任务接口 |
| 重试 | 消费者不重试、不渠道改派 | MaaS HTTP客户端/Nginx不重试，但GPUStack网关最多3次尝试；异步另有有限attempt | 已确认仅为消费者行为声明，不构成本系统禁重试要求；保持现有GPUStack策略，无需为此改造 |
| 文生图 | POST `{endpoint}/images/generations`，返回data数组 | 无API、无已接入驱动和受控运行模型 | 后续新增完整文生图能力，不能复用OCR声明已支持 |
| 图像结果 | 每项url或b64_json | artifact下载要求Bearer，不是可直接公开获取的图片URL | 首版可采用有界b64_json；若用url须验证消费者取图方式/有效期，不能直接返回私有artifact链接 |
| 图像计量 | OCR 1张，文生图按n张 | 同步成功一次计1，SQL quantity固定1；无图像张数事件 | OCR沿用一成功事件并约定单位；文生图增加图片数量口径/事件，不能继续按请求数算 |

消费者渠道中的 `assigned_node_ids` 属其自身节点配置，不能据此推断本系统可以指定GPU worker；当前本系统由GPUStack路由选副本。

## 3. 建议的OCR兼容契约

优先新增 **POST /ocr**，消费者配置 `endpoint=https://<可达私有地址>:<端口>`，provider=openai。provider在这里是消费者的协议选择，不意味着本系统必须增加另一套provider字段。

若双方统一采用 `/v1` 前缀，也可新增 `/v1/ocr`并让endpoint以 `/v1` 结尾；需要成对约定。仅调整endpoint前缀不能把固定追加的 `/ocr`变成现有 `/v1/chat/completions`，更不能转换body/response。

请求示例：

```json
{
  "model": "ocr.default",
  "image_base64": "<裸base64图片数据>",
  "return_boxes": true,
  "request_id": "consumer-business-request-001"
}
```

成功响应示例：

```json
{
  "lines": [
    {"text": "第一行文字", "confidence": 0.99,
     "polygon": [[12,12],[220,12],[220,44],[12,44]]}
  ]
}
```

实现要点：

1. 校验model、两种图片来源恰好一个、字段类型、base64与真实图片格式/解码尺寸；是否也接受data URL需写入契约。空文字图返回 `lines: []`，不能当识别失败。
2. 内部构建当前Chat请求，继续校验逻辑模型权限/pin/collector，使用同一受控同步准入，不直连gpu-ocr的原生/ocr绕过门禁。原生/ocr虽存在于后端，但无这套客户鉴权/准入/格式契约，不应直接暴露。
3. 解包后按OCR schema验证，输出顶层lines，映射confidence/polygon。缺lines视为上游格式错误（502）；缺可选字段策略遵守双方约定，不丢掉有文字的行。
4. **结果校验应在成功计量前完成。** 当前SyncIngress仅确认外层JSON合法便记同步成功；若先调用它再在外层转换失败，可能出现客户端失败却已计成功。建议抽出共享同步执行/终态与计量控制，使兼容响应验证通过后才记成功，原远端完成状态仍单独确认。
5. `JsonBodyLimit`目前只给 `/v1/chat/completions`较大预算，其他POST默认64KiB。新增/ocr必须显式配置base64 JSON限额，同时保留解码后10MiB/像素限制、输出限制和Nginx请求体预算。
6. URL输入如启用，限定协议/目标、重定向、超时与下载字节/图片尺寸；避免访问内网/元数据地址。下载阶段与推理阶段预算分开，并保证取消/超时不绕过容量与UNKNOWN判定。
7. 更新 `deploy/maas/edge.nginx.conf`白名单；TLS配置由安装脚本复制生成，部署时还须同步已生成的TLS配置，不是只改仓库文件即可生效。
8. 保留旧Chat与任务接口的行为，新增兼容测试，不把OCR改成202异步返回给消费者。

## 4. 文生图后续要求（不包含视频）

这是新增业务功能，当前不具备开通条件：

- 新增 `/images/generations`及prompt/n/size/quality/request_id schema，n默认1、范围1–10；不支持的size/quality明确拒绝，不静默忽略。
- 接入真实文生图运行时、适配器、不可变revision/镜像证明与恢复探针；当前SyncIngress仅允许gpustack.ocr，不能原样转用。
- 对齐同步完成预算；当前OCR25秒预算不能直接作为图像生成能力承诺。大任务若无法在同步链路完成，须与消费者修改协议，不能悄悄返回task ID代替data。
- 输出严格验证data各项url/b64_json、实际张数与n一致性，设总输出字节限制；10张图片的base64可能超过现有限额。私有下载链接的鉴权、有效期与可达性必须与消费者核对。
- 增加成功图片数量事件或扩展明确单位/quantity的计量；当前 `sync_usage_events.quantity CHECK(quantity=1)`按请求计数，不能直接承载n张收费。定义部分成功如何响应/计量，不根据请求n对缺失图片照算成功。
- 复用全局/project配额、维护与未知执行隔离；评估GPU显存/时延，不能因提供新URL就称为模型已上线。

## 5. 无需为此次OCR接入修改的API

- `/maas/v1/tasks`、任务查询/取消、artifact上传/下载继续为现有客户保留。
- 不需要为OCR增加 `/tasks/{id}`：该路径在消费者文档里用于视频；其外层网关自己的OCR任务由其维护。
- 不需要把本系统任务deadline提高到1天：该要求属于文档的视频异步生命周期。
- 不需要改Bearer为签名/额外必需header；消费者目前不支持额外鉴权头。
- 不需要把GPUStack管理接口、原生Playground或worker端口开放给消费者。

## 6. 幂等、错误与接入边界

消费者原子仲裁能限制其同一request_id创建重复网关任务，但**不能单独证明端到端推理恰好一次**。客户端可换request_id、存在多个渠道/实例、请求结果也可能在网络中丢失。本系统发送前持久活动与UNKNOWN规则仍必须保留。

文档7.3明确：网关生成的request_id可能不回填body，且不发幂等头。因此目前无法要求本系统每次都按request_id去重。建议：

- 首期接受可选body request_id作为有界关联字段，说明缺失时不承诺上游幂等；与本系统内部req_ID区分，不能用它决定project权限。
- 如要求上游幂等，双方约定稳定且必传的业务ID与作用域（例如project+model+request_id），持久仲裁、同内容复用/异内容409、结果保留及UNKNOWN恢复；不能只把body字段塞进内存缓存。
- 不建议采用“图片/提示词+参数哈希短窗口去重”替代业务幂等；两个合法独立请求可以输入相同，文生图还可能希望得到不同结果。
- 约定超时预算与错误JSON/HTTP映射；现有UNKNOWN通常503，不是所有非200都能当作已终止重发。文档没有完整定义OCR错误透传规则，需联调确认。

网络与证书为后续实际联调/部署事项：18832目前仅监听001回环并使用自签证书。2026-10-21用户明确本轮先不考虑连通通道、消费者部署位置及证书信任问题，因此不作为本轮API协议分析/设计的阻塞或待确认项。实际联调时再核对可达性与证书；暂缓不代表已连通，也不改变现有私有监听或开放公网。

### 已确认的重试边界（2026-10-21）

用户明确：文档“不重试、不渠道改派”只是消费者对自身行为的声明，不是对本系统的要求。不要求关闭GPUStack内部重试，不作为API兼容改造或E4的前置条件；现有网关策略、UNKNOWN保护及应用层发送约束保持不变。消费者自身去重与本系统内部尝试仍需分别描述，不能推导出端到端恰好一次。

## 7. 需要消费者补充确认的问题

1. 首期是否只发image_base64？若使用image_url，图片来自哪些受控域名、是否需鉴权/重定向？
2. base64是裸数据还是data URL；language的支持范围/语义；return_boxes默认值及坐标单位是否约定为原图像素？
3. 渠道模型名能否配置为ocr.default，而不固定为示例qwen-vl-ocr？
4. 同步HTTP等待/连接超时各是多少，是否会被其代理或worker提前取消？
5. request_id是否愿意始终透传，以及幂等范围/保留期如何定义？
6. 失败HTTP/JSON是否原样保留？
7. 图像生成是否本期必需？若是，输出b64_json是否可接受，部分张数失败如何计量？

文档2称上游路径由上游自定，但总览及4.1/4.2又写固定后缀；按其“endpoint+固定suffix”实现描述，本方案采用/ocr与/images/generations。若消费者确实支持单独submit_path配置，可另行对齐路径，但输入/输出差异仍需门面处理。

## 8. 推荐实施顺序

1. 首期仅对齐OCR：明确字段/模型/预算 → 新增兼容门面及配额/计量共用执行层 → 本地自动化契约测试。网络连通与证书信任按用户要求暂缓，实际联调/部署时再处理HTTP/TLS路由和访问配置。
2. 保留当前API并回归同步/异步、权限、暂停、UNKNOWN、大小限制、空行结果与幂等冲突。不能只测一张正常图片。
3. P4耐久期间仅本地开发/测试；如部署变更当前版本或网关策略，另开窗口、重新确认验收基线，不混入本轮8小时。
4. 文生图单列后续功能，模型/容量/计量与同步等待约束明确后再实现。
