# 单张图片内嵌异步任务

> **2026-11-02当前已发布ocr-metering-r2**：规范字段image_base64/image_url、旧别名兼容、异步远程URL及E16两个计量查询均已完成真实验收。4异步+3同步成功，7条唯一收据；当前tokens实际null/unavailable，不能按tokens实际收费。generation488、监控健康、UNKNOWN=0，8副本profile保持，无VM/worker/Docker引擎重启。OpenAPI已内嵌真实脱敏响应；以下待部署状态为历史。[发布报告](../../results/maas-ocr-release-20261102/REPORT.md)。


> **2026-11-02异步图片字段统一（待部署）**：新代码将data_url改为image_base64、url改为image_url，保持input.image层级；image_base64接受裸base64或PNG/JPEG data URL。旧字段保留弃用别名兼容，但同次重试不可换字段。76本地/105隔离通过（1旧条件跳过），现网不变。[说明](TASK-IMAGE-FIELDS.md)。


> **2026-11-02内嵌图片异步提交已部署**：应用inline-task-r1上线，generation486，接单恢复。data_url四并发同键只1任务，旧artifact方式兼容，两任务唯一尝试/成功计量和46行下载通过；同步OCR、能力schema、监控及UNKNOWN=0正常。8副本/8执行器/并发8/8、Q25/25和3.5GiB保持，无VM/worker/Docker引擎重启。下方待部署描述为历史。[发布报告](../../results/maas-inline-deployment-20261102/REPORT.md)。


> **实现完成，待部署**：本地68项、独立库/mock广泛回归78项（1条件跳过），补充后内嵌专项14项通过；无现网变更。[验收记录](../../results/maas-inline-task-20261102/REPORT.md)。

2026-11-02。用户授权设计和实现；属图片OCR完善首阶段/E14输入契约，当前计划本地与隔离验证，未授权本次直接发布现网。

新增POST /maas/v1/tasks输入形式：input.image.data_url，仅严格PNG/JPEG base64 data URL，与artifact_id二选一。保留既有artifact引用、TaskView、202新建/200幂等复用、查询/取消/下载与原指纹语义。仅gpustack.ocr支持，不开放公网图片URL或裸base64。参数仍为空。

设计：适配器公布联合输入schema，服务端校验并解码图片（默认10MiB、20MP，复用同步校验）。幂等指纹基于原请求的模型默认值展开结果，保留data_url，不基于随机生成artifact ID；完全相同请求复用原任务，不重复写文件。同图片不同编码表示不承诺等价。持现有文件生命周期锁，在同一事务的维护行锁下检查幂等、队列和存储配额，再写文件、插入输入元数据和任务。执行时数据库input规范化成artifact引用，worker无需改动。事务提交确认丢失不贸然删文件，未引用文件由现有孤儿GC延后回收。

限额：仅任务路径提高JSON上限至max_input_bytes*2以容纳base64，其他非同步路径保留64KiB；无内嵌图片的任务仍限制64KiB，避免无关参数膨胀。原入口20MiB维持，不扩大像素/原图限额。此为有界整图内存处理，不是流式上传。无数据库迁移或新增常驻服务。

验收：旧引用方式、互斥/未知字段、格式/像素/大小、模型/参数权限、同键并发只一任务一输入、不同内容409、队列/配额拒绝不写文件、提交/文件失败及GC兜底；隔离数据库+mock推理端到端，真实GPU发布另行安排。兼容文档、OpenAPI/capabilities须区分本地新代码与现网旧版本。
