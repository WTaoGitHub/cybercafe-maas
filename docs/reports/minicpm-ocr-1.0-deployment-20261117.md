# MiniCPM-OCR 1.0 当前环境部署调研

2026-11-17 · [E10 / Issue #11](https://github.com/WTaoGitHub/cybercafe-maas/issues/11) · **调研完成，尚未部署或运行模型。**

## 结论

**当前环境具备开展单卡隔离试点的条件，优先使用 worker7 的 RTX 4080 SUPER 16GB。** 驱动、架构和可用资源没有显示必须升级宿主或更换 GPU 的阻碍；16GB 下的初始化、真实解析和性能仍需实测。建议复用官方固定摘要镜像，用受控启动命令降低并发；先完成独立文档解析，再接入 GPUStack/Higress 和 MaaS。不直接替换 `ocr.default` 或 `document.unlimited-ocr-2`。

MiniCPM-OCR 的主要价值是中文/英文文档、表格、公式及拍照页面的结构化解析。它应作为文档解析候选与 Unlimited-OCR-2 比较，不能仅按参数更少、官方分数更高就替换已有服务。官方的 5090 测量不是本项目 4080 SUPER 的速度或显存保证。

## 本次现场核验

北京时间 **2026-11-17 12:20:37**，通过既有受信 SSH 通道，只读查询新 PC 及 GPUStack 管理接口。GPUStack 实际返回 `v2.2.3 / 1cfcbbf`。[脱敏证据](../../results/minicpm-ocr-1.0/20261117-research/evidence.json)保存采样值及来源。

| 节点 | 当前状态 | 判断 |
| --- | --- | --- |
| worker7 | Ready；4080 SUPER，16,376 MiB，compute capability 8.9，驱动 580.95.05；显存使用约 433 MiB、申报 0；无模型实例；24 逻辑 CPU、31.16 GiB RAM，已用约 2.31 GiB；根盘可用约 426.48 GiB | 首选独立试点；开始前再按 GPU UUID 对应管理面和宿主，不能仅凭 worker ID/主机名认定身份 |
| worker6 | Ready；同型号/驱动；Unlimited-OCR-2 instance190 正在运行，显存已用约 11.09 GiB、申报 12 GiB | 保留现有服务，不安排新模型共卡 |
| worker5 | Ready；运行正式 `maas-ocr` 两实例 | 保留正式 OCR 容量 |
| worker4 | not_ready | 缓存遥测不计入可用容量 |
| 新 PC | x86_64；8 逻辑 CPU、15.39 GiB RAM，约 9.29 GiB 可用；根盘约 270.26 GiB 可用；已有控制面、数据库、文档 API/执行器和下游服务 | 继续承担控制与持久任务；不在共享宿主安装/源码编译整套推理环境 |

GPU 数据来自管理面遥测，**本轮未直接登录 worker7 执行 `nvidia-smi`、CUDA 算子或容器运行检查**；无实例不等于没有其他 GPU 进程。旧 SSH MCP 的三个入口及旧 controller 别名连接失败后，改用项目现有受信通道，未更改连接配置或主机密钥。

本次没有创建远端容器/模型/任务，没有拉取完整运行镜像或模型权重，没有修改现有配置、挂载、驱动或服务，没有重启 VM、Docker、worker 或推理容器。

## 模型与完整解析链路

模型为 **1.3B、Apache-2.0**，原始权重为 BF16。官方仓库提供 GGUF、AWQ、GPTQ 及配套 DSpark 草稿模型；首试使用原始 BF16，暂不增加量化、Jev 质量评估器或 DSpark。授权与第三方组件许可证仍需随最终镜像清单核对。[官方模型卡](https://huggingface.co/openbmb/MiniCPM-OCR-1.0)

本次读取的发布元数据：

| 对象 | 固定值/观察值 |
| --- | --- |
| ModelScope 仓库 HEAD | `a165c0943964e167b46e32d67bbcb9deab26a8d4`；来源为用户指定仓库的 `git ls-remote` |
| Hugging Face 仓库 revision | `883ca368d17335b9ce5aa6fef4d000ef0684fcaa` |
| `model.safetensors` | 2,600,969,816 B，约 2.42 GiB |
| 两站公布的该权重 SHA-256 | `83f4e433b044fd23f9ea7eb3acbc7d59e4fe11a1fdf3c3129b7021d0ab4f8fd4` |
| 架构 | `MiniCPMOcrForConditionalGeneration` / `minicpm_ocr` |

以上是远端文件元数据及小配置读取，不是完整权重下载校验；两站整个仓库的逐文件一致性、镜像内置权重与这些 revision 的一致性均未验证。2.42 GiB 仅为权重文件大小，不是引擎峰值显存。

完整链路为：`PDF/图片 → 方向纠正与渲染 → PP-DocLayoutV3 版面检测 → 分区裁剪 → MiniCPM 识别 → 阅读顺序及 Markdown/JSON 组装`。识别 API 接受图片区域和相应任务 prompt；单独启动 `/v1/chat/completions` 不会自动提供完整 PDF 解析。SDK 与模型服务可使用独立环境，版面/方向模型可用 CPU。[官方安装与推理教程](https://github.com/OpenBMB/MiniCPM-OCR/blob/f5357c72e1605698fa857d74222c0414ca369ae3/docs/minicpm_ocr_tutorial_en.md)

官方披露的 **1.2 页/秒**来自 RTX 5090、OmniDocBench v1.6 的 1,651 页及其加速配置；图片级并发 1 仍允许页内区域并发。不能套用于本机低并发、无 DSpark 的首试，也不能与现有服务的四份小样本耗时直接比较。[官方性能口径](https://github.com/OpenBMB/MiniCPM-OCR/blob/f5357c72e1605698fa857d74222c0414ca369ae3/docs/minicpm_ocr_1dot0_en.md)

## 运行环境选择与已发现的部署陷阱

**优先固定官方镜像作为基础，再使用显式的 Python 模块入口启动 vLLM。** 不先复用 Unlimited-OCR-2 的定制 SGLang wheel，也不假定 GPUStack 内置的普通 vLLM 镜像已经支持该架构。

官方源码教程是 Python 3.12 / CUDA 13 路线，并固定 vLLM `fa3b9d1377219247060d3c8b49eae199c9e7e154` 和 Transformers `144ecf007b8b4f2910da104c84a79801db6985a6`。教程建议源码/环境/缓存预留至少 40GB。这些 revision 与稳定 pip 版本不是同一概念；另一个部署文档仍含 `xxx` 版本占位，不能直接照抄安装。当前 SGLang 指向专用、未合并 PR 分支，也不是已部署 Unlimited 的兼容证明。[官方环境定义](https://github.com/OpenBMB/MiniCPM-OCR/blob/f5357c72e1605698fa857d74222c0414ca369ae3/pyproject.toml)、[vLLM 文档](https://github.com/OpenBMB/MiniCPM-OCR/blob/f5357c72e1605698fa857d74222c0414ca369ae3/docs/vllm_deploy_en.md)

官方 Docker 路线使用 CUDA 12.9，当前驱动 580.95.05 没有显示版本门槛阻碍，仍需真实内核验证。无需据此升级宿主驱动。[官方镜像说明](https://hub.docker.com/r/openbmbofficial/minicpm-ocr)

本次只读取 registry manifest、config 和末尾小脚本层，没有下载约 10.44 GiB 的完整压缩镜像层。当前元数据为：

| 对象 | 摘要 |
| --- | --- |
| `latest` / `105-vlm-with-model` manifest index | `sha256:3cfeaf0d251ecb4e5615cf5ce97cb8c90583cfb70dc21d6978ca50a4bfc6fdd9` |
| Linux amd64 manifest | `sha256:42773d0ece292c0d8abe9edd92d5a405f37849db134c094554532880ab0d7a12` |
| config | `sha256:eaca12947aceac14cd4ffd7aba3e8ef255d8fbdb2376df4874a2572fc0c51343` |
| 压缩层合计 | 11,207,282,798 B；不是解压后磁盘占用，预留空间仍需覆盖展开层、缓存和回退版本 |

静态核验发现以下具体问题，实施时必须处理：

1. **文档与最新产物有差异。** 镜像说明仍引用 SDK 1.0.4 和旧验证摘要 `7c397ed0…`；当前 config 的构建历史记录 SDK **1.0.5**、源码 `f5357c72…`、CUDA 12.9 及专用 vLLM/Transformers 构建。构建历史不是本机运行验收，固定上述新摘要后须重新读包版本与加载结果，不能借用旧镜像的验证结论。
2. **默认启动为空闲 shell。** 当前 config 为 `Entrypoint=null`、`Cmd=["bash"]`，不能仅写 image 就期待 API 自启。继承 healthcheck 指向 `/ready`；只部署识别服务时应核验并使用 vLLM 的 `/health`。
3. **`vllm` 命令是包装脚本。** 最终 `/entrypoint.sh` 仅处理端口和 served-model-name，忽略其余 CLI 参数；内部固定 `max-model-len=40960`、`max-num-seqs=256`、batch tokens 8192，显存比例默认 0.90。追加 `--max-num-seqs 1` 不会生效。应绕过 shim，用 `/opt/venv/bin/python -m vllm.entrypoints.openai.api_server` 的受控命令设置参数，并核验实际 argv。脚本不包含 DSpark 配置。
4. **默认联网更新权重。** 最终脚本默认 `HF_WEIGHT_SYNC=1`，可能使用 HF/ModelScope 的新版本覆盖权重；已确认 `HF_WEIGHT_SYNC=0` 可关闭。固定镜像摘要还不足以固定模型；应禁用同步、只读挂载固定模型版本并校验清单。直接模块入口同时绕过这个同步步骤。

最终脚本来自 layer `sha256:66d1d1462ed67ee6730e52975e28c6a01d47b47f8b92ed9ba108537f73368a92`，下载后已验证压缩层 digest；脚本 SHA-256 为 `9cd9ccb97f6bd4c3d51293dd4e5bb20fbcfaaa7202bd96cf49f2cdd65d6da926`。以上依据当前 registry 产物静态读取，未执行其代码。[镜像 tag 元数据](https://hub.docker.com/v2/repositories/openbmbofficial/minicpm-ocr/tags/105-vlm-with-model)

## 建议的首试配置

以下是**待验证起点**，不是已验收容量或可直接发布的 GPUStack 模板：

| 项目 | 建议 |
| --- | --- |
| 资源 | worker7，单 GPU、单副本、单文档；排除其他现有服务用卡 |
| 模型 | 固定 BF16 权重和全部 tokenizer/config/template；不启用 DSpark/Jev/量化 |
| 识别服务 | 使用上述 amd64 digest；显式模块入口；核验 `--help` 后设置 context 16384、batch tokens 4096、seqs 1、显存比例 0.65、eager 模式，禁用 video；保留模型所需多模态处理和 GDN 参数 |
| SDK | 固定 1.0.5 及布局/方向模型；CPU layout/upright，区域 worker=1；明确输出 token 预算，截断按失败处理，不悄悄丢内容 |
| CPU/RAM/shm | 首试预算 4 CPU / 16 GiB RAM / 禁 swap / 独立 4 GiB shm；必须读回实际 cgroup 与容器设置，不照搬 `--ipc=host` |
| 文件与网络 | 权重只读、缓存/文档输出写独立目录；关闭权重同步；仅受控内网入口，不直接发布官方 Web Demo 到外部 |
| 完成条件 | 全部预期页面和应识别 block 完整成功、结果非空、无截断；页数或 block 失败不能记成整文档成功 |

上下文 16K 来自官方 FAQ 的降显存建议，但可能不足以处理密集区域；首试应记录失败，不能据此宣称支持任意文档。[官方 FAQ](https://github.com/OpenBMB/MiniCPM-OCR/blob/f5357c72e1605698fa857d74222c0414ca369ae3/docs/faq_en.md)

初期 SDK 可与识别服务放在 worker7 的独立环境完成端到端验证，避免新 PC 现有业务承受 Paddle/渲染负载。后续 MaaS 接入可把 CPU SDK 放入专用、有限额的文档执行器，通过 Higress 调用 GPUStack 管理的识别服务；新 PC 的 CPU/RAM 是否足够要测，不能直接往共享执行器安装 Paddle。已有 `resource_guard.py` 的 cgroup 检查思路可以复用，但入口硬编码 SGLang，不能原样用于 vLLM。

权重管理遵循[集中只读 NFS 目标](../framework/MODEL-WEIGHTS-NFS.md)：新 PC 管理版本，worker/容器只读加载，运行缓存本地可写。该 NFS 方案尚未实现。首试可以核验镜像内置权重，或准备固定本地只读版本；两者均不算统一 NFS 分发完成。后续采用 `/srv/maas/models/<固定版本>` 时，还须验证 loader、layout/upright 路径及容器可见性。

## GPUStack 与 MaaS 的实际适配范围

GPUStack v2.2.3 已有自定义后端和独立模型路由实践，可沿用镜像发布、worker 选择、鉴权、端口访问限制及健康核验。新模型先准备零副本配置，再在已核验的 worker7 上运行一个实例；实际显存 claim 根据初始化及推理峰值加裕量确定，不复用旧 OCR 的 3.5 GiB 或 Unlimited 的 12 GiB 作为已验证值。现有自动副本策略不得因新模型创建而自动获得其写权限。

推荐复用现有持久 artifacts/tasks/result/usage 生命周期，但新增独立的候选逻辑模型 `document.minicpm-ocr-1.0` 和专属 adapter。影响位置包括 `maas_platform/adapters.py` 的 registry、模型 catalog/config 校验、文档适配及结果 schema、能力文档/示例和相应契约、失败与计量测试；本轮没有修改这些实现。

现有 `DocumentOCRAdapter` 固定 Unlimited 的权重/镜像/后处理、PAGE prompt、logit processor、窗口及 schema。**不能只改 upstream_model 复用。** PDF 输入校验、页关联、文件生命周期和成功页收据可复用已存在的机制。MiniCPM 应采用独立 provenance/schema；SDK 坐标可能指向纠正后的页面，必须记录旋转和坐标系，不冒充原图坐标或伪造识别置信度。[本地适配器](../../maas_platform/document_ocr.py)、[SDK 输出约定](https://github.com/OpenBMB/MiniCPM-OCR/blob/f5357c72e1605698fa857d74222c0414ca369ae3/docs/minicpm_ocr_sdk_api_en.md)

接入前有两个必须解决的生命周期差异：

- **SDK 自带重试。** 当前 `VLMClient.recognize()` 对请求异常循环重发，context overflow 还会降低 max_tokens 重试。MaaS `max_attempts=1` 不能限制这些内部发送次数；仅把 SDK 等待时间设成 0 也不能保证禁止 context 分支重试。受控接入前须固定并核验单次发送策略，覆盖异常和 context 分支，记录实际 crop 请求次数。现有 Higress 的非幂等 5xx 重试边界也不能自动消失。未知完成请求不自动重放，超时不宣称后台 GPU 已取消。[SDK 请求源码](https://github.com/OpenBMB/MiniCPM-OCR/blob/f5357c72e1605698fa857d74222c0414ca369ae3/sdk/minicpmvocr/backends/client.py)
- **官方文档 job API 不替代 MaaS 队列。** 官方说明其 job registry 在进程内存中；成功响应还需检查区域状态。MaaS 接入优先直接调用 SDK，保留自身持久任务、租约、UNKNOWN 和唯一成功收据，避免把 Web Demo 的 job ID 当作持久完成证明。SDK/HTTP 的 `ocr` 与 `minicpm_ocr` 字段也有差异，必须以实际固定版本验收。[官方 HTTP/SDK 说明](https://hub.docker.com/r/openbmbofficial/minicpm-ocr)

页内多个 crop、方向校正调用和重试不额外计作成功图片/页面；仅整任务满足完整性条件时产生现有口径的成功页收据。token 用量若不能覆盖完整链路就明确不可用。Unlimited 的已知质量豁免不适用于此候选。

## 建议验证顺序与边界

1. **准备与冻结：** 重新只读核对 worker7 的身份、实际 GPU 进程、Docker/NVIDIA runtime、端口、资源和磁盘；镜像导入既有私仓，固定 digest，核验包版本、权重文件清单及只读挂载。先验证 CPU 导入和实际参数，再加载 GPU。
2. **独立最小试点：** 一个区域识别、一张中英页面、一张含公式/表格页面，再两页 PDF 及旋转页；同时记录初始化和推理峰值、耗时、失败/截断、显存释放及容器 OOM。发现资源不足先停候选进程检查，不升级驱动或重启 VM。
3. **可比服务检查：** 复用[已冻结的四份服务输入](../../results/unlimited-ocr-2/20261117-service-smoke/inputs.json)，相同页/DPI及完整性口径比较耗时和资源。这只能比较小样本服务行为；文字、关键数字、公式、表格和阅读顺序另需固定 GT/人工判读，不能把官方 benchmark 当作本地测分。
4. **受控接入：** 自定义 backend/Higress 验证后，新增专属 MaaS adapter，验证 PDF/图片、全页/全 block 完成、错误/超时/重试、页关联、项目隔离与唯一计量，再决定开放。原 OCR 和 Unlimited 保持独立回退路径。

本次只完成调研及静态/只读取证；镜像完整拉取、GPU kernel、16GB 加载、识别质量、吞吐、长文档、冷启动、持续负载和恢复均未验证。001/002 的禁止 VM 重启约束继续适用；不部署旧 worker-retirement 插件，也不因001曾是控制平面而排除其正常 worker 资格。
