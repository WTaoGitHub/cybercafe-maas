# SiliconFlow DeepSeek-OCR 本地测试

调用云端推理服务，本地无需部署模型。脚本仅依赖 Python 3 标准库。

将 API Key 单独放入本目录 `.mykey`（单行），然后执行：

```sh
python3 siliconflow/test_ocr.py /absolute/path/to/image.png
```

支持图片及 PDF 输入；默认结果保存到 `siliconflow/local-results/response.json`。
该目录及 `.mykey` 均已被 Git 忽略。自定义 `--output` 时请自行保护结果中的文档内容。

## 2026-10-30 实测

- 本机请求 `https://api.siliconflow.cn/v1/chat/completions`，模型 `deepseek-ai/DeepSeek-OCR`。
- 使用本地生成的 1400×620 PNG，包含中英文、订单号、数量、单价及总额。
- HTTP 200，耗时约 1.04 秒，结束原因 `stop`。
- 输入 489 tokens，输出 56 tokens，总计 545 tokens。
- 忽略空白后，识别结果与全部预期文字完全一致，包括订单号 `SF20261030001`、`15.50` 和 `31.00`。
- 这是单张合成图片的连通性与识别冒烟测试；未验证 PDF、多页、复杂版面或并发吞吐。
- 本次响应未提供费用字段；免费状态依据用户提供的平台定价信息，未通过账单核验。

原始响应、测试图片和预期文本保留在本地 `local-results/`，不提交。

## 文档

- [Chat Completions API](https://api-docs.siliconflow.cn/docs/api/chat-completions-post)
- [多模态输入与 OCR 提示词](https://api-docs.siliconflow.cn/docs/userguide/capabilities/multimodal-vision)

使用文档建议的图片 base64 数据 URL、Bearer 认证及 `<image>\nFree OCR.` 提示词。
