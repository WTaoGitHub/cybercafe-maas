# GPUStack + GPU OCR 两节点试验

## 部署

- XZ-31-001 / 10.11.31.1：GPUStack server 和 worker。
- XZ-31-002 / 10.11.31.2：GPUStack worker。
- GPUStack 固定 v2.2.3，server 镜像 digest：sha256:55ecc762950a32641b6a4d96195c0662ef72998eaeeda31bba0e8e218d41afef。
- 管理/API：001 的 loopback 和内网接口 18080，不直接发布到公网。
- 原有 cybercafe-ocr、cybercafe-h3 保留。
- 新增主容器名以 wta-gpustack 开头；GPUStack 创建的推理容器以 rapidocr-gpu 开头。
- 远程新文件和数据位于 /root/wtaobj；本地源码和脚本仅位于本目录。

## 本地访问

```sh
ssh -N -L 18080:127.0.0.1:18080 -p 2023 -i ~/.ssh/id_ed25519 root@111.4.255.126
```

然后访问 http://127.0.0.1:18080 。用户名 admin；初始密码在 001 的
`/root/wtaobj/gpustack-server/initial_admin_password`，不写入本地源码或报告。
管理脚本支持 GPUSTACK_API_KEY_FILE、GPUSTACK_API_KEY 或 GPUSTACK_PASSWORD_FILE；初始密码文件仅为首次试点的回退方式。

## 文件

- `gpu-ocr/`：CUDA 12 / ONNX Runtime GPU 1.20.2 / RapidOCR 1.4.4 镜像和 HTTP 适配层。
- `deploy-gpustack.sh`：各节点启动脚本，需要已有私有 worker-token。
- `gpustack_admin.py`：001 上创建集群、自定义后端、部署及模型路由。
- `verify_ocr.py`：OCR 请求和合成图片基线验证。
- `benchmark_gpustack.py`：同一路由的单/双副本比较。
- `probe_availability.py`：worker 离线期间的可用性采样。
- `reference/`：调研时取得的上游代码/文档快照，不是本项目实现。

## OCR 适配

普通文字提取：检测、方向分类、识别均要求会话启用 CUDAExecutionProvider；不允许整个会话静默退回 CPU。
个别 ONNX 算子仍可能由 CPU 执行。启动检查不是逐算子 GPU profile。
`/health` 返回 GPU UUID 和 provider；`/ocr` 接受 image_base64。
为接入 GPUStack，另提供 `/v1/models` 和 `/v1/chat/completions`。
chat 接口只接受 base64 图片 data URL，不下载外部 URL；非流式 assistant.content 是 OCR JSON；0.2 流式响应通过 SSE 返回纯文字和终止事件。
GPUStack 中分类为 llm 仅用于兼容路由，不使用语言模型；没有真实 token 用量，不能按 token 计费。
当前每个实例用锁串行推理，识别批量采用模型默认值；未优化跨图片 batching。
请求最大解码图片 10MB、2000 万像素；仅适用于内网试验，仍需网关请求体和并发限制。

## 复现

在两台节点构建 `gpu-ocr/Dockerfile`，标记为 `cybercafe-gpu-ocr:0.1`，再打别名：

```sh
docker tag cybercafe-gpu-ocr:0.1 quay.io/cybercafe-gpu-ocr:0.1
```

这个 Quay 名称只是预加载到两台 Docker 的本地标签，并未发布到 Quay；节点必须预先有镜像。
试验初期 001 镜像通过 Dockerfile.patch 修补；独立 Dockerfile 是最终重建路径。
worker 需挂载 /root/wtaobj/gpu-ocr 同名路径，让 local_path 模型校验通过；权重实际随 Python 包内置。
在 001 执行：

```sh
cd /root/wtaobj
python3 gpustack_admin.py cluster
python3 gpustack_admin.py deploy-ocr
python3 benchmark_gpustack.py 1
python3 benchmark_gpustack.py 2
```

为试验指定每副本 2GiB 显存申报；这只是调度预算，不是显存硬隔离，也不是实测峰值。
副本采用 spread 策略。当前 GPUStack 负责在线实例部署/路由，未实现持久异步任务、结果保留或商业计费。

## 已取得的小样本基线

输入：1200x1600，24 行合成英文发票文本；预热后计时，所有结果均返回 24 行并包含指定文字。
不是中文准确率评测，不代表完整文字逐字一致，更不代表真实业务 SLA。

| 路径 | 请求数 | 并发 | 张/秒 | 中位响应秒 |
|---|---:|---:|---:|---:|
| 原有 CPU 服务 | 8 | 1 | 0.446 | 2.208 |
| GPU 直连 | 8 | 1 | 6.179 | 0.159 |
| GPUStack 单副本 | 32 | 4 | 7.382 | 0.532 |
| GPUStack 双副本 | 32 | 4 | 11.903 | 0.284 |

双副本请求分配 001:13、002:19，相对同入口单副本吞吐约 1.61 倍。
CPU 原服务未调优，CPU/GPU 两环境依赖和线程设置不完全相同；不能视为纯硬件加速比。
没有测插座功耗、业务订单、长时间负载或单位利润。

## 离线/恢复验证

停止 002 的试验 worker 及其 OCR 容器，保持 001 和原有服务运行。
统一入口采样 60 次，19 次返回 503；最后一次失败在采样开始约 31.99 秒。
约 45.93 秒的状态采样显示 002 为 unreachable，001 持续 ready；后续样本由 001 成功处理。
这不等于无损切换，不保证其他环境具有相同检测时限；未验证超过默认 5 分钟后的跨节点副本重调度。
重新启动 002 worker 后，两节点均恢复 ready，两 OCR 副本均恢复 running。
商业异步 OCR 层仍需持久队列、超时重试和幂等任务 ID。
原始采样及基线数据位于 `results/`。

## 边界与后续

- 001 是控制单点，必须保持 Ubuntu 在线。
- 模型/数据跨 Windows 切换后的持久性尚未验证。
- 商用前还需真实中英文数据、准确率、不同图片尺寸、长时间吞吐、GPU 利用率和功耗测试。
- 本轮不启用 MQTT，不提供绘图/视频服务。
- 以后增加 worker 时应使用可访问的私有镜像仓库，替代手工预加载。

## 停止试验

先在管理端把 rapidocr-gpu 副本设为 0，再停止两台 wta-gpustack-worker，最后停止 001 的 wta-gpustack-server。
不要停止或删除原有 OCR/H3 容器，不要删除 /root/wtaobj 数据，除非确定不需要恢复。

## 真实图片测试集及手动调用

已准备 datasets/ocr-mixed-v1：100张英文文档、100张中文/混合场景图片，40张调参、160张评估。详见该目录 README.md。手动调用步骤见 MANUAL-OCR.md，客户端为 ocr_client.py。真实两张图片的功能调用结果见 results/manual-cn.json 和 results/manual-en.json。

2026-10-19 已完成200张基线评测及五组配置对比（840次计时图像处理）。报告：
- [真实图片基线](results/real-ocr-v1/REPORT.md)
- [配置优化对比](results/ocr-quality-v2/REPORT.md)
- [12张校准样本对照](results/ocr-quality-v2/comparison.html)

本轮保留线上默认配置；公开数据上的空间分组CER是工程诊断指标，不能直接当作商业准确率。

## 新模型对比（2026-10-19）

已完成 PP-OCRv6 small/medium、PP-OCRv5 server 各200张及旧v4方向处理对照。
- [新模型完整报告](results/ocr-model-v3/REPORT.md)
- [模型选择与是否测试PaddleOCR-VL](results/ocr-model-v3/DECISION.md)
- [逐图对照](results/ocr-model-v3/comparison.html)

发现旧v4存在文字行方向误判；当前线上配置未更改，暂缓第4项。

## 方向保护升级（0.3，2026-10-19）

现有 `rapidocr-gpu` 两副本已升级到 `0.3`，默认启用方向保护。
`gpu-ocr/direction_guard.py` 复用已验证逻辑：保留原文字行，先识别原方向，
对低分或分类器判为倒置的行再识别180°版本；仅在分数提高超过0.02时采用。
状态在服务锁内重置；API、模型名、JSON字段及Playground SSE协议保持兼容。
此升级没有新增90°/270°自动识别或手动旋转参数。

两节点预加载镜像后，在控制端执行 `python3 /root/wtaobj/upgrade_ocr_guard.py`。
增量构建：在 `gpu-ocr` 目录执行
`docker build -f Dockerfile.guard -t quay.io/cybercafe-gpu-ocr:0.3 .`，要求已有0.2本地镜像。
脚本保存旧版本配置、逐个替换实例并检查健康状态；旧镜像和0.2后端配置保留。
回退命令：`python3 /root/wtaobj/upgrade_ocr_guard.py --rollback`。
部署及验证记录见 `results/ocr-guard-v1/`；该升级保留原有总计两个副本，未采用压测中的四副本。

## 统一模型服务任务层（P0/P1）

已新增独立任务API、PostgreSQL持久队列、执行器与结果存储。当前仅启用mock后端；真实GPUStack key鉴权、15项PostgreSQL集成测试及独立进程SIGKILL恢复验证通过。既有同步OCR不变。

- [运行与测试说明](deploy/maas/README.md)
- [实施报告与证据](results/maas-p0-p1/REPORT.md)
- [当前设计与路线图](docs/framework/ROADMAP.html)
