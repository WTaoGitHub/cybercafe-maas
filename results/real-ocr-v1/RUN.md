# 复现本轮基线

在 wtaobj 中安装依赖：`.venv/bin/pip install -r requirements-evaluation.txt`。
API key 从 `.gpustack-api-key` 读取，不写入报告或日志。

在单独终端建立转发（18081 避免占用 Playground 的 18080）：

```sh
ssh -N -L 127.0.0.1:18081:127.0.0.1:18080 -p "$MAAS_SSH_PORT" -i "$MAAS_SSH_KEY" "${MAAS_SSH_USER}@${MAAS_SSH_HOST}"
```

运行：

```sh
.venv/bin/python -m unittest test_evaluate_ocr -v
.venv/bin/python evaluate_ocr.py --split calibration --output results/real-ocr-new
.venv/bin/python evaluate_ocr.py --split evaluation --output results/real-ocr-new
```

默认目录为 results/real-ocr-v1；已有逐图响应会复用并重新评分，不会重新请求。比较新模型必须使用新输出目录，避免混用旧响应。
`report_ocr.py` 当前固定读取 results/real-ocr-v1，运行 `.venv/bin/python report_ocr.py` 可重新生成本轮报告。
本轮没有自动重试；中途因零面积参考框导致的评分中断已修复，已保存的 OCR 响应被复用，无需重复推理。
