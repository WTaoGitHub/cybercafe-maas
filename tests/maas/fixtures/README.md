# 三模型实机验收样本

PP 的同步、异步入口和 Ovis 的异步入口共用[固定图片](../../ovisocr3/fixtures/service-report.png)。核对标题、报告编号 `QSR-2026-0410`、数量 `128`、时长 `12 minutes`、状态 `Complete` 及阅读顺序。每个成功请求的图像用量应为 1。

[固定 PDF](ocr-service-verification.pdf) 包含两张合成页面。每页标题是 `Service Verification`。第一页内容为 `ALPHA Report` 和 `Reference 2048`，第二页为 `BRAVO Report` 和 `Reference 4096`。

在仓库根目录运行 `python3 -c "import hashlib, pathlib; print(hashlib.sha256(pathlib.Path('tests/maas/fixtures/ocr-service-verification.pdf').read_bytes()).hexdigest())"`，期望值为 `00e2ea4a71ef5fe1219d7589844507291fa5a22ad7672bd0f22ae7b64485b14c`。实机验收需要运行中的 MaaS 测试入口、已授权的测试项目以及就绪的 GPUStack 模型。按[文档任务调用步骤](../../../deploy/maas/DOCUMENT-OCR.md)上传 PDF 并提交 Unlimited 异步任务，从结果文件逐页核对文字与顺序，确认成功页数为 2、成功计量事件只有一条。
