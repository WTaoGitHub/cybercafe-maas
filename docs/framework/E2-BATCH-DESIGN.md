# E2 OCR 批次处理设计

首版设计已定稿，待实现。跟踪 [Issue #3](https://github.com/WTaoGitHub/cybercafe-maas/issues/3)。

## 首版范围

面向离线 OCR。一个批次绑定一个逻辑模型及其 revision 和配置指纹，每项对应一张 PNG/JPEG 和一个现有异步任务。上限建议 100 项，可配置。批次先接纳清单，再逐步把条目送入现有任务队列。GPUStack 仍处理单图推理，现有 worker、并发限制、租约和用量收据用于子任务。首版支持同项目已上传的 `artifact_id`，下游负责多选上传及界面。

借鉴 OpenAI Batch 与阿里云百炼的清单、批次资源和逐项关联方式。MaaS 清单使用原生 OCR 参数，成功和失败清单可单独下载，ZIP 供一次下载。完成时限与容量由 MaaS 配置和实测确定。

## API 契约草案

请求、响应和示例见 [主 OpenAPI YAML](../api/openapi.yaml)。

| 接口 | 行为 |
| --- | --- |
| `POST /maas/v1/artifacts` | 复用图片上传，并为批次清单增加 UTF-8 JSONL 媒体类型和独立大小限制 |
| `POST /maas/v1/artifacts/batch` | 一次上传多张 PNG/JPEG，逐文件返回 `artifact_id` 或错误 |
| `POST /maas/v1/batches` | Bearer 鉴权与 `Idempotency-Key`，请求指定 `model`、`input_file_id` 和可选 `timeout_seconds`，首次返回 202，重放返回已有批次 |
| `GET /maas/v1/batches/{id}` | 返回状态、计数、deadline、批次错误及结果文件 ID |
| `GET /maas/v1/batches/{id}/items` | 游标分页，按固定输入序号列出条目、`custom_id`、子任务 ID、状态和错误 |
| `POST /maas/v1/batches/{id}/cancel` | 幂等登记取消，返回当前批次状态 |
| `GET /maas/v1/artifacts/{id}` | 下载成功 JSONL、失败 JSONL 或可选 ZIP，沿用同项目授权 |

所有端点沿用已登记 Bearer 凭据和项目隔离，跨项目资源按不存在处理。首版的重试由客户端从错误清单挑选可重试项，用新清单和新幂等键提交。相同 project 和幂等键对应相同请求指纹时复用批次，指纹不同返回 409。鉴权后先查项目内幂等记录并比较请求指纹，仅新请求校验输入文件和额度。首次接纳须在事务内再次检查幂等键。记录随批次元数据保留，至少覆盖批次未终结阶段和输出下载窗口。

清单示例。每个 `custom_id` 在批内唯一，作为业务关联标识。重用同一图片引用的不同条目仍是独立请求。

```jsonl
{"custom_id":"page-001","input":{"image":{"artifact_id":"art_example_a"}},"parameters":{"return_boxes":false}}
{"custom_id":"page-002","input":{"image":{"artifact_id":"art_example_b"}}}
```

提交前整体校验 UTF-8、JSONL 结构、项数、单行字节数、图片引用、媒体类型、参数及模型授权。校验失败返回行号和错误码，整批不接纳。验收时覆盖跨项目、过期引用、重复 ID 和超限文件。清单先有界解析，再在接纳事务中复核引用和额度并建立文件保留关系。

## 批量上传图片

OpenAI Batch 通过 Files API 上传单份 JSONL，指南建议用远程图片 URL 控制清单体积。是否接受 Base64 Data URL 取决于目标端点。阿里云百炼也上传单份 JSONL，多模态清单中的媒体 URL 要求公网可访问。两份 Batch 指南均未列出一次上传多张原图的专用接口。MaaS 延续清单与图片分离的结构，用同项目私有 `artifact_id` 引用图片。 [OpenAI Batch](https://developers.openai.com/api/docs/guides/batch) · [OpenAI Files API](https://developers.openai.com/api/reference/resources/files/methods/create) · [阿里云百炼 Batch](https://www.alibabacloud.com/help/en/model-studio/batch-inference)

`POST /maas/v1/artifacts/batch` 接收 `multipart/form-data`。恰好一个 JSON `metadata` 部件，可出现在任意位置，包含唯一的 `client_file_ids` 数组。同名 `files` 部件按相对顺序与 ID 一一对应。服务端在暂存时检查总字节和文件数，请求级校验通过后逐文件校验、写入并返回结果。单文件损坏、超限或存储配额不足可作为该项错误。成功项保留，客户端可以单独补传失败项。整批创建仍统一校验完整清单。

```bash
curl "${MAAS_BASE_URL}/maas/v1/artifacts/batch" \
  -H "Authorization: Bearer ${MAAS_API_KEY}" \
  -F 'metadata={"client_file_ids":["page-001","page-002"]};type=application/json' \
  -F 'files=@page-001.png;type=image/png' \
  -F 'files=@page-002.jpg;type=image/jpeg'
```

批量上传的单图字节数、请求总字节和文件数分别配置。客户端可分多次上传以组成一个 OCR 批次。每次上传独立创建文件，`client_file_id` 用于关联本次响应。整次响应丢失后重传可能产生新 artifact ID，未引用文件按普通输入 TTL 回收。

### 待议：JSONL 内嵌图片

记录候选字段 `input.image.image_base64`，与 `artifact_id` 二选一，可考虑沿用单任务接受裸 Base64 和 PNG/JPEG Data URL 的约定。内嵌可减少少量小图的提交步骤，但会增加清单体积并占用单行预算。后续需明确允许的编码、单行与整清单字节上限、解码后单图上限，以及校验和落盘时机。当前契约中的 JSONL 图片仍使用 `artifact_id`。

## 状态、调度与恢复

批次正常路径为 `QUEUED → RUNNING → FINALIZING → COMPLETED`。批次详情返回 `total`、`pending`、`active`、`succeeded`、`failed`、`cancelled`、`expired`，除 total 外各计数互斥且总和等于 total。完成表示必需清单已发布，单项仍可能失败。批次级永久错误使用 `FAILED` 和结构化错误。子任务保留当前状态机，批次条目额外区分尚未派发和因批次到期未派发。

- 批次和清单条目在同一事务中持久化。表按批次和序号关联，并约束批内 `custom_id` 唯一、每项最多一个子任务。凭据只保存绑定 ID，worker 按现有流程取 Key、重新认证与授权。
- 协调器按项目轮转，每轮有界地领取可派发条目。子任务创建和条目关联在同一事务完成，使用行锁和唯一约束抵御协调器并发与进程退出。复用现有接纳锁和额度检查，批次子任务同时受全局和项目队列约束。预留普通异步任务的队列空间，保留同步与异步共享运行槽的规则。
- 单独限制每项目和全局的未完成批次、待派发条目和关联文件字节数，防止清单在任务队列外无限堆积。触及配额时对新批次返回 429。既有批次等待可用额度，子任务期限不得超过批次 deadline。
- 冻结批次模型 revision 和配置指纹。派发时发生配置漂移或凭据撤销，停止派发并为受影响项记录原因。已派发任务仍按现有规则处理。子任务的 UNKNOWN 继续遵守已有隔离，协调器不自动补发或解除隔离。
- 取消进入 `CANCEL_REQUESTED`，停止派发并取消未派发项，已派发任务调用现有取消逻辑。到期使用同一收敛流程，未派发项标记为 expired。取消和到期与派发在批次锁内仲裁，首个停止原因决定批次终态。
- 已完成项保留。执行中的子任务继续收敛，可能因后端状态不明而失败。所有条目终结且清单发布后，批次进入 `CANCELLED` 或 `EXPIRED`。运行期限限制继续派发和执行，发布部分结果允许在期限后完成，不能据批次终态推断上游已确认停止。

### 结局锁定和条目分类

| 事件 | 原子决策 |
| --- | --- |
| 最后一项收敛，尚无停止原因 | 锁定 COMPLETED，转 FINALIZING |
| 批次尚未收敛，收到取消或到期 | 首个在批次锁内登记的停止原因生效。停止派发，收敛后锁定 CANCELLED 或 EXPIRED，再转 FINALIZING |
| FINALIZING 收到取消或到期 | 返回当前快照，沿用已锁定的执行结局 |
| 两份清单发布成功 | 进入已锁定终态 |
| 清单发布失败 | 有界重试发布。耗尽后以 FAILED 和批次级发布错误结束，保留逐项状态及尚在保留期的单任务结果 |

到期前已有终态的条目保留原状态。到期后，未派发项和已派发但因 deadline 未完成的项计为 expired。后端活动不明或结果未提交等执行失败继续计为 failed，保留原错误码。到期协调器主动取消的子任务若正常取消，批次条目映射为 expired。取消触发的正常取消计为 cancelled。以批次停止原因和子任务终态为依据进行确定性映射。对 UNKNOWN 禁止自动补交。

## 文件与计量

成功清单每行包含 `custom_id`、`task_id`、`result_artifact_id` 和 `usage_id`。失败清单包含相同关联字段中已存在的部分，并给出状态、错误码和 `retryable`。两份清单按输入序号输出，空清单为零字节。ZIP 用有界后台打包生成，包含清单和 OCR 结果 JSON，清单固定为 `success.jsonl` 和 `errors.jsonl`，结果路径固定为 `results/{result_artifact_id}.json`。ZIP 功能关闭时返回 `DISABLED`。ZIP 超限或失败时单独报告打包错误，清单仍可下载。

批次接纳时保留清单与输入，子任务提交成功时建立批次对结果文件的保留关系，避免早完成结果在打包前被 GC 清除。输入保留到相关条目收敛，结果保留到批次输出到期。接纳时仍检查原始有效期。接纳后，批次派发、worker 读取、打包和授权下载均识别对应的有效保留关系，GC 采用相同判断。读取和保留的检查与删除互斥。输出 TTL 从两份清单原子发布时开始，ZIP 沿用该到期时间。发布永久失败时为已生成结果设置有限的补救下载窗口，期满释放保留。与现有 GC 协调，使用保留记录管理引用并计入存储配额。打包设置条数、总字节、运行时间、临时空间和并发上限。发布文件与批次引用按现有持久化和孤立文件回收模式处理，重跑发布不得产生重复可见产物。

子任务成功时继续写现有用量收据。批次接纳、取消、查询和打包不生成推理收据。需要批次汇总时读取子任务收据，图片计量汇总成功图片数。token 汇总须标明覆盖范围，缺失项不能算零。已成功项重放创建请求不新增任务和计量，调用方用新幂等键主动重新推理属于新任务。

## 实施顺序与验收

| 阶段 | 交付 | 完成条件 |
| --- | --- | --- |
| 1. 接纳与查询 | 数据迁移、JSONL 校验、输入保留、幂等、详情与分页 | 并发重放只接纳一次。原清单过期后可用原键取回批次。无效清单整批拒绝，跨项目查询返回 404。 |
| 2. 派发与收敛 | 复用队列和 worker、项目轮转、预留普通任务槽位、取消和到期 | 事务前后进程退出可恢复。验证取消、到期和结局锁定竞态，以及输入原 TTL 到期后仍能派发。凭据撤销、模型变更和 UNKNOWN 收敛可解释。 |
| 3. 核心结果交付 | 原子发布成功和失败 JSONL，结果保留、到期回收和计量关联 | 验证部分成功、全失败、取消或到期后部分结果、发布恢复和超限。打包前及发布后保留期内结果可读，无重复用量。 |
| 4. 独立增量 | 批量图片上传及 ZIP 下载，可分别交付 | 验证 multipart 元数据前置或后置、错配零发布、逐项失败和补传。验证 ZIP 路径映射、打包超限、失败后 JSONL 仍可下载。 |
| 5. 容量与发布验收 | 入口白名单、契约状态更新、完整调用示例及混合负载验证 | 独立测试库覆盖 1 项、100 项及超限。多项目与同步、普通异步混测，记录普通任务排队、批次吞吐、锁等待、发布耗时和存储峰值。XZ-31-001 与 XZ-31-002 禁止 VM 重启。 |

阶段 1–3 构成首版核心闭环。阶段 4 不阻塞核心链路联调。配置默认值和性能阈值在独立环境按容量基线确定，再进入发布验收。
