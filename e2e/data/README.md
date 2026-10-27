# 可复用OCR小样本

本项目生成的白底文字图片，无客户数据，不包含字体文件。

- ocr-bilingual.png：1200×240，RGB PNG。
- ocr-bilingual.jpg：同一画布，JPEG quality=95。
- manifest.json：尺寸、SHA256、原始文字及预期片段。

图片文字为两行：

```text
MAAS E2E TEST 2026
中文文字识别测试
```

采用48px字体生成，用于PNG/JPEG接入、中英文基本识别、上传下载完整性等正常回归。OCR-006断言MAAS及中文两个片段存在，不将此小样本视为真实业务准确率基准。大规模精度回归仍使用既有DocLayNet/RCTW测试集。

无文字PNG由e2e/fixtures.py在运行时确定性生成；恶意超像素样本仅修改头部尺寸，用于验证服务在完整解码前拒绝，不能作为可识别的正常图片。
