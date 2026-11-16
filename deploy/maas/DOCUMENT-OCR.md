# 文档异步任务接入

P4–P6实施中，尚未部署M2。复用现有 `/maas/v1/artifacts`、`/maas/v1/tasks`、任务取消、结果下载和usage查询；不新增PDF专用API。模型服务M1见[验收报告](../../results/unlimited-ocr-2/20261116-m1/REPORT.md)。

PDF预处理使用 `requirements-document.txt` 固定的pypdfium2；不改变旧图片OCR的10 MiB/20MP契约。PDF要求真实格式、无加密且1–10页；150 DPI默认、最高300 DPI，每页最多10MP、合计50MP，超出预算拒绝，不静默丢页或降采样。图片经校验和EXIF方向归一化，输出坐标依据归一化后的页面尺寸。

PDF解析/渲染在独立进程执行：Linux地址空间512 MiB、CPU时间15秒、单文件64 MiB、64个文件描述符；外层检查墙钟期限（检查5秒、渲染30秒）和任务取消。每进程最多一个预处理任务，忙时返回429。Linux父进程退出会杀死预处理子进程；正常、取消及错误路径清理私有临时目录。部署文档容器还须使用有界tmpfs及cgroup，进程被强杀后由容器重启清除其临时卷；此约束不代表VM重启验收。

本机测试：安装 `requirements-maas.txt` 和 `requirements-document.txt` 后执行 `python -m pytest tests/maas/test_documents.py -q`。测试覆盖真实PDF渲染、损坏/加密/超页数/像素预算、取消清理及图片方向；Linux资源限制和实际GPU/下游链路仍须单独验收。

新逻辑模型为 `document.unlimited-ocr-2`，登记模板见 [catalog.document.example.json](catalog.document.example.json)。模型、SGLang运行镜像、上游后处理器和PDF渲染profile固定版本，并纳入既有任务模型指纹。任务省略重试次数和期限时，分别使用1次和900秒；显式重试次数必须为1，总期限不得超过900秒。模型执行600秒预算包括本地预处理、网络和后处理；心跳与任务租约沿用公共执行链。

文档只支持异步artifact引用，不增加公共同步、内嵌、URL或多文件接口。请求示例（先上传PDF取得实际ID，入口使用运维提供的文档试点URL）：

```json
{
  "model": "document.unlimited-ocr-2",
  "input": {"document": {"artifact_id": "art_REPLACE_WITH_UPLOADED_ID"}},
  "parameters": {"dpi": 150},
  "timeout_seconds": 900,
  "max_attempts": 1
}
```

上传 `POST /maas/v1/artifacts` 使用 `Content-Type: application/pdf` 和原始PDF字节；新任务 `POST /maas/v1/tasks` 必须提供稳定的 `Idempotency-Key`。原键及原请求重发仅返回原任务，不自动重跑推理。沿用查询、取消与结果下载路径。示例完整流程见 [verify_document_task.sh](../../examples/verify_document_task.sh)。

结果JSON的 `outputs.schema_version` 为 `unlimited-ocr-2.document.v1`，包含输入顺序的页面、渲染尺寸和源尺寸、版面块、原始标记与Markdown。块的bbox在方向归一化后的渲染页面中使用0–1000坐标，未提供行置信度。首版不生成裁剪图，转换器的悬空图片链接替换为 `[image omitted]`，只登记一个结果artifact。下游显示原文或Markdown时应按不可信文档内容处理。

任务、结果artifact、原任务计量事件和新页收据沿用同一成功事务。最终收据通过既有 `/maas/v1/tasks/{id}/usage` 或 `/maas/v1/usage/{id}` 查询，包含 `successful_pages`；`usage_type=pages` 只返回页数。任务查询默认选取该模型的页单位，旧图像模型继续默认images；单位不匹配返回422。token窗口汇总语义未验证，因此 `tokens.status=unavailable`、原因 `DOCUMENT_WINDOW_USAGE_UNVERIFIED`，不能按0计费。失败、取消、结果未提交或UNKNOWN均不生成成功页收据。

发布时使用独立任务数据库、artifact目录、绑定文件及执行器，预算固定为单份文档执行。旧OCR使用原入口与原配置；独立库使新模型UNKNOWN隔离不阻断旧模型。新模型不自动加入副本调节器，也不自动放开所有本地Key。GPUStack的All models权限与MaaS本地binding分别校验。
