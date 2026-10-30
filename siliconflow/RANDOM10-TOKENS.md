# 10张随机测试图片：DeepSeek-OCR token用量

2026-10-30。本机调用SiliconFlow的deepseek-ai/DeepSeek-OCR。对ocr-mixed-v1完整200张清单，使用random.Random(20261030).sample抽取10张，不缩放、不重编码；4张DocLayNet文档、6张RCTW街景。抽样用于粗估，不是精度或吞吐测试。

串行10次全部HTTP 200、finish_reason=stop，无输出截断，无重试。提示词为`<image>\nFree OCR.`；temperature=0、max_tokens=4096。输入usage包含图片、提示词及协议开销；输出用量仅代表本次OCR文本，未验证识别完整性。

| 用量 | 每图平均 | 最小–最大 |
|---|---:|---:|
| 输入tokens | 844 | 699–1099 |
| 输出tokens | 271.1 | 3–1100 |
| 合计tokens | 1115.1 | 912–1799 |

样本平均文件大小约0.82MB（十进制），约467万像素；用量汇总为输入8440、输出2711 tokens。

| 类型 | 张数 | 平均输入tokens | 平均输出tokens |
|---|---:|---:|---:|
| 文档 | 4 | 699 | 640.5 |
| 街景 | 6 | 940.7 | 24.8 |

粗估建议：本轮混合样本可先按每图约850输入、270输出tokens；纯文档按约700输入、650输出tokens。样本少且本轮街景占60%，不能当成所有图片的通用常数；不适用于其他模型的图片token编码。上下文8K并不表示每张图片固定使用8K。

原始OCR输出、样本身份和summary.json留在被Git忽略的`local-results/random10-tokens-20261030/`。仅提交无原始图像/识别文本/凭据的汇总。工具同步修正JPEG输入的data URL MIME类型，历史PNG请求行为不变。
