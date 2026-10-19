# 运行说明

本轮使用 XZ-31-002 独立无网络容器，镜像 quay.io/cybercafe-gpu-ocr:0.2（本地预加载镜像名）。未改线上 GPUStack 模型、路由或副本。
远程目录 `/root/wtaobj/ocr-experiments`：dataset/、run_variants.py、results/。
本地脚本 `/Users/4paradigm/dev/cybercafe/wtaobj/ocr-experiments`。

独立实验命令形式（重新运行请使用新的容器名和输出目录，防止复用既有响应）：

```sh
docker run --rm --name wta-ocr-quality-new --gpus all --network none \
  --entrypoint python3 \
  -v /root/wtaobj/ocr-experiments:/experiment \
  quay.io/cybercafe-gpu-ocr:0.2 /experiment/run_variants.py \
  --dataset /experiment/dataset --output /experiment/results-new \
  --split calibration \
  --variants A_default B_resolution1536 C_tiles1024 D_score030 E_rotate90
```

评估集使用 `--split evaluation --variants A_default B_resolution1536 C_tiles1024 D_score030`。
脚本对已存在的逐图JSON跳过推理；每组预热一次，返回全图坐标，记录耗时、引擎调用数和去重数。

本地 `.venv/bin/python ocr-experiments/analyze.py` 读取 results/ocr-quality-v2/raw 并沿用 evaluate_ocr.py 的冻结指标。
本地 `.venv/bin/python ocr-experiments/make_report.py` 生成报告和配对bootstrap比较。
单元测试 test_variants.py 在具有 cv2/NumPy 的同一实验镜像中运行；覆盖窗口边界、坐标映射、空间去重。原评测器4个测试亦保留。

本轮不调用管理API，不读取或传输GPUStack API key。容器无网络，不会下载新权重；使用镜像内已部署的同一组 ONNX 文件。
