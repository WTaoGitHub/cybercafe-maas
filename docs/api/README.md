# MaaS 对外 API 文档（当前版本）

> **OpenAPI / Swagger文档（2026-11-02）**：[openapi.yaml](openapi.yaml) / [openapi.json](openapi.json) · [使用与校验说明](OPENAPI.md)。覆盖当前已实现的9个客户操作，不包含尚未开放的`/v1/models`或一步上传建任务。

> 当前基线：API consumer-ocr-r1、执行器/Observer e3-r1、GPUStack v2.2.3。自建collector和动态preflight门禁已退役；推理复用GPUStack的服务管理能力，保留鉴权、配额、维护、UNKNOWN及成功计量。

文档更新：2026-10-29；当日只读核对001的应用版本、四执行器及运行额度，接口/监听沿用此前消费者兼容发布。本次未部署或新增接口。消费者兼容契约见[OCR对接](CONSUMER-OCR.md)，容量验证见[E3试点报告](../../results/maas-e3-completion-20261029/REPORT.md)。

**当前提供：消费者兼容同步OCR、原Chat同步OCR、异步OCR任务、输入/结果文件上传下载、任务查询/取消、模型能力查询。** 不包含E1候选接口；无文生图、音频、视频、批次或ZIP接口。

## 1. 接入信息

### 1.1 Base URL

```text
https://<管理员提供的私有访问地址>:<端口>
```

当前001 TLS监听回环18832，原HTTP监听回环18831，尚未开放公网。经批准的本地SSH转发后，TLS示例Base URL为 `https://127.0.0.1:18832`。127.0.0.1指调用进程所在主机/容器，不能直接填入远程消费者配置来访问001。

当前证书为自签证书，需导入管理员提供的公共证书并验证；不要禁用TLS校验。接入方不需要GPUStack管理员账号或宿主机root权限。

2026-10-27新增同机Docker专用HTTP入口：当前下游kc-e2e-aliyun-slave3网络（172.18.0.0/16）的容器使用 **http://172.18.0.1:18831**。该地址是001的Docker桥网关，不是容器自己的127.0.0.1；仅允许指定桥接来源，仍要求Bearer鉴权并沿用本文路径/限额。无需TLS证书，仅适用于该受控同机网络，不是公网Base URL。原回环HTTP/TLS不变；其他网络须独立配置，不能假设host.docker.internal自动可用。详见[接入与回退](../../deploy/maas/CONTAINER-HTTP.md)。

### 1.2 鉴权与项目

所有下述业务接口要求：

```http
Authorization: Bearer <registered_inference_api_key>
```

key须由管理员登记到MaaS credential/project/逻辑模型，并在GPUStack侧有效且有相应模型权限。仅有任意GPUStack key不够，不接受管理key替代客户key。

资源按project隔离：同project不同key可访问该project的已知task/artifact ID；跨project通常返回404。客户端 `X-Project`、`X-User-ID`等头不用于选择身份。模型提交/推理/能力查询另校验模型权限。

当前业务面向一个下游服务：最终用户、用户Key、用户限流、套餐和收费由下游负责；下游使用服务持有的已登记推理Key接入MaaS，不为每个最终用户复制一份MaaS绑定。`project`是MaaS的资源归属，不是GPUStack cluster或下游用户账号。多份服务Key可以绑定同一项目，额度共享，不按Key叠加；当前并未实现独立MaaS Key与GPUStack内部Key解耦。

项目及额度由管理员交付时告知，正常请求不需提交项目名。当前没有“我的项目/额度”查询接口。技术测试/维护探针可保留独立绑定，不自动合并既有project或迁移历史任务。[职责、额度与交付约定](../framework/SERVICE-ACCESS-POLICY.md)。

没有自助注册、客户key创建、客户额度查询或管理员HTTP业务接口。

### 1.3 通用约定

- JSON请求使用 `Content-Type: application/json`；文件上传是原始字节而非JSON/multipart。
- 当前私有Nginx只放行GET/POST及本文业务路径；不允许query参数。路径和模型名大小写须按示例。
- 通常返回 `X-Request-ID: req_<随机值>`，由本系统生成；不会把客户端的同名头当作幂等键。请在支持请求中保留此ID。
- 时间字段为ISO8601带时区字符串；示例时间、识别文字、revision、耗时与节点都是格式示意。
- Nginx/TLS等入口错误可能不是本文JSON结构，客户端须先检查HTTP状态和Content-Type。

## 2. 接口列表

所有路径相对于Base URL，不需要再拼接一个额外 `/v1`。

| 方法 | 路径 | 成功状态 | 用途 |
|---|---|---:|---|
| POST | `/ocr`、`/v1/ocr` | 200 | 消费者兼容同步OCR，顶层lines；详见[契约](CONSUMER-OCR.md) |
| POST | `/v1/chat/completions` | 200 | 原Chat同步OCR，stream=false |
| POST | `/maas/v1/artifacts` | 201 | 上传输入文件 |
| GET | `/maas/v1/artifacts/{ident}` | 200 | 下载同project的未过期输入或结果 |
| POST | `/maas/v1/tasks` | 202 / 200 | 新建异步任务 / 幂等复用 |
| GET | `/maas/v1/tasks/{ident}` | 200 | 查询已知任务 |
| POST | `/maas/v1/tasks/{ident}/cancel` | 200 | 取消或请求取消 |
| GET | `/maas/v1/models/{name}/capabilities` | 200 | 查询已授权模型的schema与预算 |

当前正式模型为 `ocr.default`，driver为gpustack.ocr；不要把原生GPUStack路由名或示例qwen-vl-ocr当作本系统已登记模型。没有公开模型列表或任务列表接口。

## 3. 同步OCR

### 3.1 新接入推荐：POST /ocr 或 /v1/ocr

单张图片直接提交，HTTP200返回顶层 `lines`，不需要先上传artifact：

```json
{
  "model": "ocr.default",
  "image_base64": "<PNG或JPEG的裸base64>",
  "return_boxes": true,
  "request_id": "consumer-business-001"
}
```

也可将image_base64替换为image_url（公开HTTP(S)图片地址）；两个来源恰好一个。返回示例：

```json
{"lines":[{"text":"中文 English","confidence":0.99,"polygon":[[12,12],[220,12],[220,44],[12,44]]}]}
```

无文字返回 `{"lines":[]}`，仍为成功。request_id仅关联日志，不去重；完整字段、图片下载限制、可运行示例见[消费者OCR契约](CONSUMER-OCR.md)。

### 3.2 原Chat入口：POST /v1/chat/completions

请求体：

```json
{
  "model": "ocr.default",
  "stream": false,
  "messages": [
    {"role": "user", "content": [
      {"type": "image_url", "image_url": {"url": "data:image/png;base64,<BASE64_IMAGE>"}}
    ]}
  ]
}
```

| 字段 | 要求 |
|---|---|
| model | 必填，已授权的逻辑模型名 |
| stream | 省略视为false；只能false，不支持SSE |
| messages | 使用上例单张PNG/JPEG的base64 data URL结构 |

JPEG使用 `data:image/jpeg;base64,...`。`image_url.url`此处不支持远程HTTP图片地址。顶层image_base64、image_url、language、return_boxes不属于本节Chat契约；需要这些字段请使用[消费者兼容/ocr](CONSUMER-OCR.md)。prompt仍不属于OCR契约；后端忽略字段不代表支持其语义。

成功返回Chat completion envelope：

```json
{
  "id": "ocr-example",
  "object": "chat.completion",
  "created": 1792557600,
  "model": "<upstream-route-name>",
  "choices": [
    {"index": 0, "message": {"role": "assistant", "content": "{\"text\":\"示例文字\",\"lines\":[{\"box\":[[0,0],[100,0],[100,20],[0,20]],\"text\":\"示例文字\",\"score\":0.98}],\"elapsed_ms\":120.0,\"node\":\"GPU-node-identifier\"}"}, "finish_reason": "stop"}
  ]
}
```

`choices[0].message.content`是**JSON字符串**，需要第二次JSON解析，内容格式见第8节。网关可能重写响应model为上游路由名，不应依赖它回显ocr.default。OCR没有真实token用量，响应不承诺提供token usage。

同步请求等待OCR完成，无持久task ID、结果下载ID或同步幂等保证。超时/断连时后台可能已经执行，不能自动重发来“确认”。目前正式模型预算25秒，受控同步代码上限为 `min(model.max_runtime_s, 120)`；并非所有请求都可等待120秒。

## 4. 文件上传与下载

### POST /maas/v1/artifacts

必需Authorization；图片OCR上传时Content-Type用image/png或image/jpeg，body为文件原始字节：

```http
POST /maas/v1/artifacts
Authorization: Bearer <key>
Content-Type: image/png

<raw PNG bytes>
```

201响应：

```json
{
  "id": "art_0123456789abcdef0123456789abcdef",
  "size": 12345,
  "sha256": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
}
```

- size为字节数，sha256为上传字节哈希；响应不包含公开URL或expires_at。
- artifact层还允许text/plain、application/json、application/octet-stream，**不表示OCR能处理这些类型**；OCR执行要求PNG/JPEG。
- 文件须非空且不超过输入限额；上传阶段不是完整图片/模型输入有效性证明，后续推理仍可能拒绝。
- 上传没有幂等保证；同文件重传可产生新ID并占用存储。上传成功不自动创建任务。

### GET /maas/v1/artifacts/{ident}

返回原始文件内容，而非包裹在JSON中的下载URL。响应Content-Type为已登记媒体类型；异步OCR结果为application/json。同时设置：

```http
Cache-Control: no-store
Content-Disposition: attachment
```

仅同project可读。不存在/无权限通常404；已删除或TTL过期410/RESULT_EXPIRED（输入和结果都使用此错误码）；文件暂不可读503/ARTIFACT_UNAVAILABLE，完整性异常503/ARTIFACT_CORRUPT。没有承诺匿名、Range或长期有效的公开下载链接。

## 5. 异步任务提交

### POST /maas/v1/tasks

额外必需请求头：

```http
Idempotency-Key: client-business-request-001
```

请求体：

```json
{
  "model": "ocr.default",
  "input": {"image": {"artifact_id": "art_0123456789abcdef0123456789abcdef"}},
  "parameters": {},
  "timeout_seconds": 300,
  "max_attempts": 1
}
```

| 字段 | 必需 | 说明 |
|---|---|---|
| model | 是 | 非空逻辑模型名，最长128字符 |
| input | 是 | OCR要求image.artifact_id；引用同project已上传、未过期的input artifact |
| parameters | 否 | 默认{}；当前OCR只接受空对象 |
| timeout_seconds | 否 | 默认120，范围1–3600秒；自接纳起的总截止，包含排队 |
| max_attempts | 否 | 默认3，范围1–3；有限attempt预算，不允许重放不确定执行 |

请求包络及OCR input/parameters禁止未定义字段。不能在input放远程图片URL或直接base64。示例300秒是显式设置，单次模型执行仍受模型预算限制。

新任务返回202；同project同幂等键且请求语义一致返回原任务200，两者都设置 `Location: /maas/v1/tasks/{id}`。共同响应结构TaskView：

```json
{
  "id": "task_0123456789abcdef0123456789abcdef",
  "model": "ocr.default",
  "revision": "registered-ocr-revision",
  "state": "QUEUED",
  "attempts": 0,
  "result_artifact_id": null,
  "error_code": null,
  "deadline": "2026-10-21T11:05:00Z",
  "created_at": "2026-10-21T11:00:00Z",
  "updated_at": "2026-10-21T11:00:00Z"
}
```

202只表示持久接纳，不保证已经推理或最终成功。排队/存储超额、身份/输入错误会拒绝；维护暂停不一定拒绝异步提交，但会停止派发，排队仍消耗deadline。

### 幂等规则

- Idempotency-Key必需、1–128字符，作用域为project+key；不是body request_id或X-Request-ID。
- 同键、相同经schema归一化的请求返回原任务；同键不同内容409/IDEMPOTENCY_CONFLICT。建议始终保存并重发完全相同body。
- 新上传产生新artifact ID，属于不同请求内容；不能带新ID复用旧幂等键。
- 幂等复用返回当前任务状态，可能已经成功/失败/取消，不会重新推理。鉴权、模型目录、存储检查等前置条件仍可能拒绝请求，不能理解为无条件缓存命中。
- 提交响应丢失：用原键+原body重查；若已有task ID，优先直接GET。不要换键自动重做UNKNOWN相关失败。

## 6. 任务查询与取消

### GET /maas/v1/tasks/{ident}

200返回上述TaskView的当前值。没有内嵌result，SUCCEEDED时从result_artifact_id另行下载。建议约1–2秒轮询，客户端须设总等待边界；没有WebSocket、webhook或完成回调。

| state | 含义 | 客户端处理 |
|---|---|---|
| QUEUED | 等待派发 | 等待，注意总deadline |
| RETRY_WAIT | 等待有限重试 | 等待，不另建重复任务 |
| RUNNING | 执行器已领取 | 继续查询 |
| CANCEL_REQUESTED | 已请求运行中取消 | 仍可能有后台GPU活动 |
| SUCCEEDED | 结果/成功事件已提交 | 下载result_artifact_id |
| FAILED | 失败终态 | 查看error_code；不能一律重做 |
| CANCELLED | 取消终态 | 不代表任意历史远端活动均已确认结束 |

attempts为领取次数，不一定等于真实GPU执行次数；维护导致的未发送延后也可能影响观察值。普通HTTP GET成功200也可能表示所查任务已FAILED，应检查state。

UNKNOWN不是TaskView的state，而是内部远端活动状态；任务可能已FAILED/error_code=UNKNOWN_BACKEND_ACTIVITY，系统仍阻止新的推理。任务终态不是GPU已空闲证明。

### POST /maas/v1/tasks/{ident}/cancel

无需JSON body，200返回TaskView：QUEUED/RETRY_WAIT转CANCELLED，RUNNING转CANCEL_REQUESTED，已有终态保留。重复取消不会新建任务。任务不存在或跨project返回404。

取消不保证立即终止GPU；客户端中止请求/停止轮询也不会自动取消任务。没有删除任务或手工重试API。

## 7. 模型能力查询

### GET /maas/v1/models/ocr.default/capabilities

200响应字段：

| 字段 | 含义 |
|---|---|
| model | 逻辑名ocr.default |
| revision | 当前登记的版本字符串 |
| execution_modes | 当前实现返回["async"] |
| max_runtime_s | 目录中的单次运行预算，当前正式OCR为25秒 |
| input_schema | JSON Schema，OCR为image.artifact_id引用结构 |
| parameters_schema | JSON Schema，当前OCR空对象，additionalProperties=false |

**兼容性说明：当前execution_modes只列async，但系统另有第3节同步入口。** 本文忠实记录这一实现差异，不擅自将字段改成sync/async。查询不是实时GPU健康探针，也不是全模型列表。未知模型404/MODEL_NOT_FOUND，无模型权限403。

## 8. OCR结果字段

同步content第二次解析后，或异步结果的outputs中：

```json
{
  "text": "示例文字",
  "lines": [{"box": [[0,0],[100,0],[100,20],[0,20]], "text": "示例文字", "score": 0.98}],
  "elapsed_ms": 120.0,
  "node": "GPU-node-identifier"
}
```

text为合并文字；lines为行级文本/框/置信度，空文字图可返回空列表。score范围0–1，不是识别正确保证；box来自识别坐标，当前不提供文档结构或语义还原。node为后端节点/GPU标识，不是客户选择worker的参数。elapsed_ms是后端OCR测量值，不含整个异步排队，也不是计费GPU秒。

异步下载artifact外层：

```json
{
  "outputs": {"text": "", "lines": [], "elapsed_ms": 120.0, "node": "GPU-node-identifier"},
  "model_revision": "registered-ocr-revision",
  "attempt": 1,
  "usage": {"unit": "task", "quantity": 1}
}
```

上例为无文字结果的字段示意。三类接口的结果结构不同：

| 接口 | 解析方式 | 行字段 |
|---|---|---|
| /ocr、/v1/ocr | 直接读取响应JSON的lines | text、可选confidence/polygon |
| /v1/chat/completions | 将choices[0].message.content再次JSON解析 | text、score、box |
| 异步结果artifact | 下载JSON并读取outputs.lines | text、score、box |

confidence/polygon是兼容同步门面的转换字段，不会改变原Chat及异步结果。当前没有文生图data数组或检测语言字段。

## 9. 限制、保留与失败语义

下表限额已与2026-10-29的001 API/执行器/observer配置核对；单次模型预算取当前登记目录。部署配置可以调整，不构成吞吐/SLA承诺；模板和代码默认值可能与现部署不同。

| 项目 | 当前值/行为 |
|---|---|
| 上传输入 | 默认10MiB；OCR只支持PNG/JPEG，后端解码上限2000万像素 |
| 同步JSON | /ocr、/v1/ocr和Chat默认20MiB（含base64开销）；其他非上传POST默认64KiB |
| Nginx请求体 | 20MiB；各层更小的有效限制优先 |
| 上游输出/保存结果 | 默认20MiB |
| 并发 | 当前全局4、每project4，同步/异步共享；当前4个单任务执行器。代码默认仍为全局2/项目1，不随执行器数自动调整 |
| 等待队列 | 当前全局1000、每project100；统计QUEUED/RETRY_WAIT，满额新任务429/QUEUE_FULL |
| 存储 | 当前全局10GiB、每project2GiB未删除artifact元数据字节 |
| 输入TTL | 默认上传起24小时；活动任务引用期间清理保护 |
| 结果TTL | 默认结果创建起24小时，过期下载410 |

单个项目的有效上限取全局/项目两者较小值，当前为最多4个执行槽、100个等待任务和2GiB已登记artifact。其他项目也共享全局额度，项目上限不是保底资源。每项目额度当前是统一配置，尚无按项目单独定制的政策表。执行槽统计异步RUNNING/CANCEL_REQUESTED与同步DISPATCHING；排队时间计入任务总deadline。异步202是持久受理，不代表已开始或已结束执行。

活动任务可继续读取已过上传TTL但被引用保护的输入；客户下载接口仍按TTL判断，不能据此认为输入永久可下载。任务记录和结果TTL分开：任务SUCCEEDED不保证24小时后结果仍存在。没有公开删除artifact接口，未引用上传等待TTL清理。

受控维护或任一UNKNOWN会阻止新的推理；不会因此删除已有结果。当前全局维护，不能承诺按worker局部继续服务。MaaS客户端/Nginx不做推理重试，但GPUStack网关可尝试多个副本；不承诺网络推理恰好一次。

## 10. 错误格式与处理

常规业务错误：

```json
{
  "error": {
    "code": "SYNC_CAPACITY",
    "message": "Synchronous admission capacity reached",
    "retryable": true,
    "request_id": "req_example"
  }
}
```

| HTTP/代码 | 含义 |
|---|---|
| 400 IDEMPOTENCY_REQUIRED | 未提供有效异步幂等键 |
| 401 UNAUTHENTICATED | 缺少或未登记key |
| 403 AUTH_DENIED / AUTH_REVOKED / MODEL_FORBIDDEN | 上游拒绝、撤销或模型无权限 |
| 404 NOT_FOUND / INPUT_NOT_FOUND / MODEL_NOT_FOUND | 资源/有效输入/模型不存在或不可访问 |
| 409 IDEMPOTENCY_CONFLICT / MODEL_REVISION_CHANGED | 幂等内容冲突或固定版本变更 |
| 410 RESULT_EXPIRED | 输入/结果已到期或删除 |
| 413 INPUT_SIZE / REQUEST_SIZE / IMAGE_PIXELS | 上传、JSON、解码字节或兼容OCR像素超限 |
| 415 MEDIA_TYPE / IMAGE_FORMAT | 上传媒体类型不允许或兼容OCR格式不支持 |
| 422 INPUT_INVALID / SYNC_INPUT / SYNC_UNSUPPORTED | schema错误、同步格式或模型不支持 |
| 422 IMAGE_BASE64 / IMAGE_INVALID | 兼容OCR的base64或PNG/JPEG内容无效 |
| 422 IMAGE_URL_NOT_ALLOWED / IMAGE_URL_REDIRECT / IMAGE_FETCH_FAILED / IMAGE_ENCODING | 图片URL受限、重定向、下载失败或HTTP压缩不支持 |
| 408 IMAGE_FETCH_TIMEOUT | 兼容OCR输入准备超时，未发送推理 |
| 429 SYNC_CAPACITY / QUEUE_FULL / STORAGE_QUOTA | 并发、等待队列或存储额度不足 |
| 503 MAINTENANCE_PAUSED / BACKEND_QUARANTINED | 同步准入暂停或未知执行隔离 |
| 503 UNKNOWN_BACKEND_ACTIVITY | 同步远端完成未确认，不能盲目重发 |
| 503 AUTH_UNAVAILABLE / BINDING_INVALID / STORAGE_FULL / SERVICE_UNAVAILABLE | 鉴权、配置、磁盘或内部服务不可用 |
| 502 OUTPUT_INVALID / OUTPUT_SIZE / OUTPUT_ENCODING | 同步上游响应格式/大小/编码异常 |

PREFLIGHT_STALE、PREFLIGHT_UNAVAILABLE属于已退役基线，当前正常推理路径不依赖这些门禁。

此表为常见错误，不是穷举。同步UPSTREAM_REJECTED可能保留被认可的上游4xx；某些版本/网关校验还有专用代码。异步执行失败通常体现在查询所得TaskView.error_code，而不是查询HTTP直接返回上述错误状态。

retryable表示错误类型的建议，不是无限重试许可。同步发送结果不明请保留关联信息并联系运维；异步仅用原Idempotency-Key+原body确认接纳，已知任务优先GET。没有通用Retry-After承诺或自动跨渠道切换。

## 11. 计量及非客户接口

异步每成功任务一个持久事件；受控同步每服务端成功请求一次事件，维护探针不计。同步客户端重发可能新增成功事件；服务端成功不代表客户端收到响应。无真实token计量、商业扣款或客户账单API。

以下不属于本文对外业务接口：

- API私有18830的 `/healthz`、`/docs`、`/openapi.json`：Nginx业务入口未放行；health仅检查API/DB。
- GPUStack原生 `/v1/models`、管理UI/Playground、worker `/ocr`与 `/health`：内部管理/诊断路径，不受本文客户契约保证。
- 私有CLI、metrics/alerts文件、维护/备份工具：无客户HTTP路由。
- `/images/generations`：尚未实现；`/ocr`与`/v1/ocr`已上线，协议边界见[消费者API分析](../framework/CONSUMER-API-GAP.md)。

更完整的可执行Python调用示例见 [普通用户手册](../handbooks/USER.md)；密钥开通见 [管理员手册](../handbooks/ADMIN.md)。

说明：capabilities查询描述异步TaskAdapter的input/parameters及execution_modes，不是消费者同步门面的schema；同步字段以[消费者OCR契约](CONSUMER-OCR.md)为准。

## 12. 可选择的端到端验收

当前接口可使用[模块化E2E框架](../../e2e/README.md)回归，支持按case_id、模块、label筛选和全目录执行，输出JSON/JUnit/Markdown。真实推理和数据写入需显式启用；测试沿用当前鉴权，不自动修改授权、维护或部署。详见[65项用例目录](../../e2e/CATALOG.md)。
