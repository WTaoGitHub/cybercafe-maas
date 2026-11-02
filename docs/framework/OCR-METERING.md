# E16：OCR成功图片与token用量交付

2026-11-02，用户要求新增增强并着手首阶段。商业价格、余额、订单、最终用户及收费归下游；MaaS交付一次成功的可核对用量事实。

首阶段：当前单图OCR成功图片数=1，独立持久计量ID、model/revision、成功时间、任务/同步来源；与成功事件同事务提交。保留异步结果原usage={unit:task,quantity:1}，增量添加metering；同步原body不变，用X-Usage-ID返回查询凭据（仍需Bearer授权）。新GET /maas/v1/usage/{ident}查询已知计量ID；GET /maas/v1/tasks/{ident}/usage按已知任务查询，跨project不可见。不新增公开列表或价格/收费接口。结果文件过期不删除用量记录；首版不自动清理计量账本，生产长期保留策略需后续另定。

token模式仅使用获批准上游Chat响应usage.prompt_tokens/completion_tokens的非负整数真实报告，两项必须完整；可选total_tokens须一致。映射input_tokens/output_tokens、status=available、source=upstream_reported；0仅用于真实上游报告0。缺失、无效时null并说明unavailable及原因，不按字符/base64/像素估算，不影响OCR成功图片数。当前PP-OCRv4代码未报告tokens，因此当前模型不能据此实际按tokens收费；下游需据状态选择图片模式。将来接入原生token模型须再验证其计量定义。

同步HTTP重发仍可能成为新推理和新用量，不宣称持久幂等（E4未实现）。任务幂等重用/反复下载不新建用量；失败、取消、UNKNOWN和内部恢复探针不产成功计量。历史成功任务不按新语义追溯补账，无新记录返回404/USAGE_NOT_AVAILABLE，不把未知token或旧事件推算成新账单。

新增数据库迁移、API/白名单、OpenAPI及文档/测试，仅本地/独立数据库验收，不自动发布现网。测试涵盖唯一提交、失败/取消/UNKNOWN/probe排除、零token与无效token、跨项目、文件过期后查询、同步/异步回归及迁移历史边界。后续多图/PDF页数与音视频单位跟随E14/E10/E15，按需扩展。
