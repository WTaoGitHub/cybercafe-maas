# Unlimited-OCR-2 P2 隔离 GPU 功能试点

2026-11-16 · [Issue #11](https://github.com/WTaoGitHub/cybercafe-maas/issues/11) · [P1](../20261116-p1-build/REPORT.md)

P2 在独立 worker 6 上完成 BF16 单请求模型加载与自建样本功能检查，并在无在途请求时重启试点容器后再次真实解析。单页、2、10、11 页的页标记与固定内容通过；这是隔离功能证据，不是业务质量、容量或 M1 网关验收。

## 环境与生效配置

目标 RTX 4080 SUPER、16376 MiB、驱动 580.95.05、compute capability 8.9；worker 原生身份 id 6 的 name/external_id 与 GPU UUID 一致、Ready。初始无 GPU compute app，正式 OCR 在另一 worker。固定模型 revision 与文件摘要见 [P1 文件清单](../20261116-p1-build/model-manifest.json)。最终镜像 config `sha256:4b4106ea05bfde4cab05c44bf2f3dd8d5e2c6d742a6652a0e2a6554d938107d2`，私仓 manifest `sha256:f86ed85cbf7676e6168339e7f617ebe96025f202ff7fc59d2c628d52e7760c70`；worker 使用已有 reader 和 CA 拉取，config 和18层匹配，已命中本机缓存，不是冷拉。

镜像由 P1 的 `f46141ad…` 派生，增加 `python3-dev`。容器限额 16 GiB RAM、4 CPU、4 GiB `/dev/shm`，模型只读，宿主只在 loopback 发布试点端口。实际启动 `python3 -m sglang.launch_server`，显式 BF16、`--max-running-requests 1`、`--mem-fraction-static 0.6`、`--chunked-prefill-size 2048`、`--max-prefill-tokens 2048`、`--disable-cuda-graph`、`--tokenizer-worker-num 1`，使用 `unlimited-ocr` chat template、fastokens、custom logit processor、streaming session 和禁用 radix cache；GUNDAM/BASE 窗口均为3页，SAM bias上限64 MB，共享内存包装开启，模型及 Transformers 离线模式开启。窗口10、CUDA graph开启、30/100页均未验证。

最小 BF16 CUDA 矩阵运算和 Triton JIT 算子在目标卡通过；服务日志显示 v2 模型实际加载，首次权重加载28.10秒、第二次1.25秒，各报告权重占用6.38 GB（引擎日志原单位），不是总显存。第二次容器 StartedAt 至 warmup ready 约24秒。未建立独立冷缓存环境，不把首次/第二次加载分别等同严格冷/热启动。FFmpeg 缺失使可选 torchcodec 音视频组件报加载警告；本次图片/文档路径成功不证明音视频支持。GPU使用默认 MoE kernel 配置，未进行性能优化。

## 样本与结果

样本由本目录脚本生成，字体为 worker 上 Liberation Sans 与 Droid Sans Fallback；[SHA-256 清单](sample-sha256.txt)冻结本次图片。测试为串行单执行请求；原始响应与日志仅保存在私有目录。首批客户端未精确记录请求起止，不能据轮询时间推算延迟。重启后的公开单页客户端整体运行于 06:07:45–06:07:54 UTC，含容器启动，不是纯推理耗时。

| 样本 | 核验结果 |
| --- | --- |
| 单页密集英文报告 | SSE DONE、finish=stop、1页标记；标题、128、12 minutes、参考编号均匹配；重启后同样通过，原始输出逐字节一致 |
| 2、10、11页报告 | 每档均为一个多图请求，DONE/stop；页标记分别为2、10、11，唯一参考号在对应页段且顺序正确，续页文字存在；10和11页各由4批窗口完成 |
| 空白页 | 1页标记、无实际文字；模型仍输出一个空 image 版面块，属于布局误检，不可据此生成指向不存在裁剪图的 Markdown 链接。初次探针误将结构头当文字；只对已保存响应离线修正规则后核对，未重放 |
| 90°旋转页 | 标题和参考号匹配，DONE/stop、1页标记 |
| 表格和公式页 | 表头及 Paper→12/24、Books→5/50 的行列关联正确；`F = m x a` 原样保留 |
| 中英混排 | 中文标题、中文句子与英文参考号匹配 |
| 独立短文串行 | ALPHA 和 BETA 的独立参考号分别匹配且未出现对方编号，未发现这两个样本间的内容泄漏 |

多页整文档 max_tokens 分别为3000、15000、16500，外层总时限900秒，页级上限20000；所有请求只有一次已确认成功的响应，没有重放未知请求。以上仅检查固定合成样本的内容和页关联，不等于跨页表格合并或留出业务样本的 CER、阅读顺序与质量验收。

## 故障与资源边界

初始候选模型加载后内置 warmup 因 `Python.h` 缺失导致 Triton 编译失败、调度器退出；即使容器退出码为0也未判为成功。未提交文档；追加头文件修复、Triton JIT、重新发布/reader 验证后，完整 warmup 和真实样本成功。补丁对循环引用的效果由两次成功加载支持，但不能由此证明所有负载均无泄漏。

首次启动及各探针的390个2秒GPU样本观测峰值为10776 MiB；第二次 StartedAt 后50个样本观测峰值10022 MiB，宿主最低可用24548624 KiB。各次 `/dev/shm` 2秒采样最大观测值84 KiB；采样可能错过瞬时峰值，也不能证明共享内存传输未回退 inline。未开展并发压测，未验证共享缓存清理、冷拉或整机重启。

采样器和客户端均已退出，隔离试点模型容器仍运行供后续对照；旧 worker 和正式 OCR 未停止。P3 仍需验证 GPUStack 后端、受信模型权限、Higress 数据面及实例替换，原始自定义 processor 反序列化路径不能向普通下游开放。
