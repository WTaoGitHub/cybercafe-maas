# 三模型实机验收样本

PP 的同步、异步入口和 Ovis 的异步入口共用[固定图片](../../ovisocr3/fixtures/service-report.png)。核对标题、报告编号 `QSR-2026-0410`、数量 `128`、时长 `12 minutes`、状态 `Complete` 及阅读顺序。每个成功请求的图像用量应为 1。

[固定 PDF](ocr-service-verification.pdf) 包含两张合成页面。每页标题是 `Service Verification`。第一页内容为 `ALPHA Report` 和 `Reference 2048`，第二页为 `BRAVO Report` 和 `Reference 4096`。

在仓库根目录运行 `python3 -c "import hashlib, pathlib; print(hashlib.sha256(pathlib.Path('tests/maas/fixtures/ocr-service-verification.pdf').read_bytes()).hexdigest())"`，期望值为 `00e2ea4a71ef5fe1219d7589844507291fa5a22ad7672bd0f22ae7b64485b14c`。实机验收需要运行中的 MaaS 测试入口、已授权的测试项目以及就绪的 GPUStack 模型。按[文档任务调用步骤](../../../deploy/maas/DOCUMENT-OCR.md)上传 PDF 并提交 Unlimited 异步任务，从结果文件逐页核对文字与顺序，确认成功页数为 2、成功计量事件只有一条。

下载该任务的结果文件和 `GET /maas/v1/tasks/{id}/usage` 返回的完整收据后，可用 Python 3 校验样本文字、顺序、计量单位、数量及两处收据 ID。每次冷启动和重建分别保存一对响应，传入实际模型名。计量事件唯一性另按同一任务 ID 在测试库核对。

```sh
python3 tests/maas/fixtures/verify_ocr_result.py document.unlimited-ocr-2 result.json receipt.json
```
