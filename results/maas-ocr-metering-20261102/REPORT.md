# E16：当前OCR计量首阶段实现与隔离验收

2026-11-02。状态：图片计量及真实上游token接收/查询代码完成，本地/隔离通过，**未部署现网、未执行真实GPU与生产数据库迁移**。当前PP-OCRv4没有真实token报告，token计费前置未满足，不宣称两种收费模式在当前模型均可实际结算。

## 实现

新增006_ocr_usage_records.sql，在原MaaS数据库创建独立成功OCR收据账本；不改原成功事件表、不回填历史，不另建服务。异步新结果保留usage={unit:task,quantity:1}并增加metering；任务状态、结果及旧成功事件和新账本同事务提交。同步响应原body不变，以X-Usage-ID关联持久收据；账本与同步成功事件同事务提交。

新增GET /maas/v1/usage/{ident}和GET /maas/v1/tasks/{ident}/usage，按已知ID、同project鉴权查询，返回no-store；已过期或清理的artifact不影响账本。待执行、失败、取消、UNKNOWN、探针或历史无收据记录均不伪造成功图片用量；无可见记录404/USAGE_NOT_AVAILABLE。没有列表/导出/价格/扣款API。

收据包含稳定id、source_type/source_id、model/revision、successful_images=1、tokens及recorded_at。空文字成功图片仍为1；recorded_at为收据构建时间，不是GPU时长、精确提交时刻或金额。同步仍无持久幂等，重复成功推理产生新收据，客户端按usage ID去重不能替代E4。

Token仅读取有效上游Chat usage.prompt_tokens/completion_tokens非负整数；若提供total_tokens须一致。真实0保留，缺失或非法时数量为null，status=unavailable，注明UPSTREAM_USAGE_ABSENT/INVALID。不按字符、像素、base64或图像张数估算。当前RapidOCR/PP-OCRv4服务源码gpu-ocr/server.py没有usage/token报告，因此实际运行预期为unavailable，不能把null当0或免费。

仅已支持单图Chat请求及可验证OCR lines输出生成新的同步收据；旧Chat任意JSON透传的既有HTTP/body行为保持，不将未知输出或多图冒充成功单图片。原旧成功事件语义不追溯改变，未合条件则无X-Usage-ID。原生GPUStack内部重试不计为多个有效产出；不承诺整链路物理执行exactly-once。

## 验证

- 本地79项通过：token缺失/非法/真实零与正数、稳定ID、非可验证Chat排除，以及OCR连接器/门面、入口模板回归。
- 独立PostgreSQL/API+mock推理广泛回归129项通过、1项旧条件跳过（host侧数据库中断harness未提供）。
- 补充no-store及迁移历史边界后，E16集成专项15项通过，包含前轮重复，不累加为144个独立用例。
- 异步成功图片+token报告、无报告及无效报告，查询与结果一致，幂等/重复读取不重复计量；同步/ocr、/v1/ocr、Chat响应体兼容且可按响应头查询。
- 跨项目404；结果文件过期/GC后仍可查询收据；待执行/取消/失败/UNKNOWN/探针无新成功收据；旧成功记录不回填。
- 新账本写入失败时整个成功提交回滚，没有成功状态或旧成功用量先行落库；后续故障恢复须遵循既有未知活动规则，不自动重试。
- OpenAPI3.1共11个代码操作、入口白名单及JSON/YAML一致性校验通过。新增两个查询仅改仓库模板，现网仍是9个操作。

隔离数据库独立于生产MaaS/GPUStack，所有推理和token报告mock；不能将合成token测试当成当前真实OCR已有token输出。私有日志存Mac忽略目录，不入Git。无生产代码/配额/模型/监听/数据库变更，无VM/worker/Docker引擎重启。

## 下一步及边界

受控发布需要备份并独立核对迁移，更新应用及必要入口模板，保持8副本profile并验证真实图片成功用量、token不可用、历史记录和查询。退回旧应用可以保留新表及收据，不自动DROP。新表暂无TTL，生产需明确保留/容量/备份策略。

下游仍需决定价格规则、采用图片或token还是组合，不由MaaS直接收费。同步响应丢失可能导致调用方拿不到usage ID；首版没有账单列表/游标导出或响应找回，不能承诺完全自动对账，需后续E16/E4完善。Token实际收费还需原生token服务，或另行明确定义业务token计量方案；未经讨论不增加估算器。

[设计与契约](../../docs/framework/OCR-METERING.md) · [OpenAPI](../../docs/api/openapi.yaml)
