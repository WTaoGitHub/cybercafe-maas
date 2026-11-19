# PP-OCRv4 在标准 NFS worker 上启动通过

2026-11-19 · [Issue #64](https://github.com/WTaoGitHub/cybercafe-maas/issues/64) · [原故障 OVIS3-INC-003](../ovisocr3/20261118-deploy/REPORT.md#故障与处理边界)

在现有 NFS 共享根补齐 `image-contained/model/deployment.json`。文件内容与 Bootstrap 的 `MARKER` 定义一致，共 138 bytes，权限 0644，SHA-256 为 `9eb9ea995210428ec49ada03af1aecd31be9ed32a56529c733a6d3f822fec0b6`。文件在导出目录外写好后原子发布。PP-OCRv4 权重继续由原推理镜像提供。 [NFS 发布约定](../../deploy/bootstrap/nfs-server/README.md#镜像内置权重的占位目录)

| 验证项 | 现场结果 |
| --- | --- |
| 路径可见性 | worker8 宿主、GPUStack worker 容器和临时推理容器均从只读 NFSv4 读到相同摘要的标记。 |
| 复测部署 | 复用 `maas-ocr` 的固定后端版本及 `local_path`。临时 GPUStack 模型15限定到标准 NFS worker8，GPU 由 GPUStack 选择，实例200达到 1/1 Ready。正式 `maas-ocr` 的 worker 和 GPU 选择器继续为空。 |
| 推理 | 从临时推理容器回环调用 `/health` 和 `/v1/chat/completions`，均返回 HTTP 200。复用[合成图片](../../tests/ovisocr3/fixtures/service-report.png)，四项预期文本全部命中，完成原因 `stop`，耗时 0.357 秒，检测、分类和识别均为 CUDA。 |
| 清理后复核 | 临时模型、实例、容器、监听端口和实例日志均已移除。测试没有创建网关路由或 MaaS 任务。正式模型的配置摘要、实例 ID 和路由台账与验证前一致，PP-OCRv4 2/2、Unlimited 1/1、OvisOCR3 1/1 Ready。 |

发布过程使用已有 NFS export，目录新增后宿主和容器即可访问。验证全过程未重启 worker、Docker 引擎或 VM。保留标准 NFS worker 启动时读取占位目录的依赖。
