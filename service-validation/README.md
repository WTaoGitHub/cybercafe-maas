# OCR 方向、服务吞吐与 GPU 电费验证

本目录为独立测试实现；原有 rapidocr-gpu 部署不改动。模型为 PP-OCRv4 和 PP-OCRv6 small，同一进程按请求选择实现，状态由服务锁保护。

## 方向策略

- `v4_legacy`：旧版文字行分类器直接旋转其判断为180度的行。
- `v4_upright`：不调用文字行方向分类，适用于已经摆正的输入。
- `v4_guarded`：先保持原始文字行识别，对分类器判180度或识别分数低于0.5的行再识别倒置版本；仅当倒置分数高出原分数0.02以上时采用。置信度不是正确率，仍需按业务图片验收。
- `v6_small`：官方v6 small检测与识别模型，关闭额外方向分类。

`rotation_cw` 表示在识别前将输入顺时针纠正多少度，允许0/90/180/270；预测框会映回请求图片的原始像素坐标。它是调用方提供的纠正参数，不是自动识别出的角度。本轮按原始像素方向处理，没有新增EXIF自动转正逻辑。

临时 OpenAI 请求通过顶层 `ocr_variant` / `rotation_cw` 或文字段 `ocr_variant=v4_guarded;rotation_cw=0` 选择；后者可经过通用代理。普通接口 `/ocr` 接受 image_base64 及这两个参数。

## 测试

- test_engine.py：旋转坐标互逆、像素恢复、避免错误旋转、恢复低分倒置行。
- direction.py：32张固定图片×4个角度，以及显式纠正；补齐guarded的200张原方向数据。共872次图像处理；评分前统一映回原图坐标。
- http_checks.py：经GPUStack检查模式切换、实际PNG旋转纠正、SSE与非流式一致、缺图400。
- load.py：经GPUStack入口测试并发与持续负载；按来源固定20张评估图片，每个矩阵单元40请求（每张两次），无自动重试。
- warm_workers.py：双节点及多副本矩阵前直接预热每个实例的全部选中输入。持续负载另外预热。
- telemetry.py：主机侧每0.5秒读取GPU板卡功耗，记录主机时间；不修改功率上限或驱动。
- summarize_load.py：按请求窗口积分功耗。默认成本计入两张GPU，包括单工作节点测试时控制机闲置GPU；另保存实际工作GPU和相对空闲基线的增量值。

## 环境及构建

worker002用已有 cybercafe-ocr-model-benchmark:3.7.0 缓存环境（Dockerfile.cached）。worker001从既有0.2基础镜像构建（Dockerfile），通过requirements-service.txt固定CPU依赖，使用本地wheels离线安装且不做依赖解析，保留GPU版ONNX Runtime。

未重装主机CUDA。两节点已实际加载的libcublas均为基础CUDA镜像中的12.1.0.26，cuDNN Python包均为9.1.0.70。已有可用CUDA库不再重复安装冗余pip NVIDIA包。

构建上下文为wtaobj，模型权重来自model-benchmark/models/下的官方固定revision。wheels从worker002生成后保存在本目录，避免worker001低速外网重复下载；requirements-missing-001.txt记录其相对基础镜像的缺少/差异包。

```sh
docker build -f service-validation/Dockerfile -t quay.io/cybercafe-ocr-validation:0.1 .
```

镜像名为本地预加载标签，并非已发布的公共Quay仓库。

manage.py在控制端用现有私密管理凭据创建临时后端、模型及推理专用key；key只允许测试路由，测试结束删除并吊销。不要输出key文件，不提交凭据。

## 成本口径

用户指定1元/kWh，每台每天8小时，本轮只计算GPU电费。持续负载按英文文档/街景约1:1混合；日处理量为两节点闲置时段重合、全时有请求时的理论外推，不是订单量或保证容量。未计CPU、主板、电源损耗、制冷、网络、人工或折旧。
