# 复现

1. 本地 `python3 model-benchmark/download_models.py` 下载六组官方检测/识别权重，记录 revision 与 SHA256；权重位于 model-benchmark/models，提交源码时忽略。
2. 基于本地预加载 quay.io/cybercafe-gpu-ocr:0.2 镜像，用 model-benchmark/Dockerfile 和 requirements-runtime-lock.txt 复现新环境。本轮是在该基础镜像的独立容器安装 PaddleOCR3.7.0 / PaddleX3.7.2；没有修改线上容器。
3. 远程 /root/wtaobj/ocr-experiments 中保留 dataset/、models/、model-provenance.json、run_models.py。
4. 将该目录挂载到独立 GPU 容器 /experiment 后，运行：

```sh
python3 /experiment/run_models.py --variant M1_v6_small
python3 /experiment/run_models.py --variant M2_v6_medium
python3 /experiment/run_models.py --variant M3_v5_server
python3 /experiment/run_models.py --variant A_v4_no_cls
```

每组默认运行 calibration40 + evaluation160，额外预热1张，不自动重试。已有逐图JSON会跳过；复测使用新的 --output，避免复用旧响应。
`A_default` 引用上轮独立容器的200张原始响应，作为线上旧模型的参考；新补跑的 `A_v4_no_cls` 用于排查关闭方向分类的影响。

拷回远程 model-results/ 的内容到本地 results/ocr-model-v3/raw/。在 wtaobj 中运行：

```sh
.venv/bin/python model-benchmark/validate_results.py
.venv/bin/python model-benchmark/analyze_models.py
.venv/bin/python model-benchmark/make_report.py
```

正式计时从BGR数组解码完成后开始，到文本和原图坐标结果组装完成，不包含模型加载、图片解码、网络/网关。候选和旧模型的前后处理实现仍存在差异，本轮是整条OCR实现对比，不能将全部差异归于权重。

追加方向复核：在相同镜像中运行 `python3 /experiment/check_direction.py`，保存 raw/A_v4_cls_recheck；单张原因诊断脚本为 diagnose_direction.py。本轮总计1000次新计时处理（600候选+400对照），另引用历史原配置200张。诊断页不计入计时比较。

XZ-31-002已保存运行环境镜像 cybercafe-ocr-model-benchmark:3.7.0，镜像ID见runtime-image.txt。复用该缓存镜像时显式覆盖入口：

```sh
docker run --rm --gpus all --network none --entrypoint python3 \
  -e PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK=True \
  -v /root/wtaobj/ocr-experiments:/experiment \
  cybercafe-ocr-model-benchmark:3.7.0 \
  /experiment/run_models.py --variant M1_v6_small --output /experiment/model-results-new
```

独立实验容器已移除，模型和原始结果保留。模型4未运行。
