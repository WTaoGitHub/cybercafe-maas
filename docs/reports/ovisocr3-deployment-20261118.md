# OvisOCR3 部署调研

2026-11-18 · [E10 / Issue #11](https://github.com/WTaoGitHub/cybercafe-maas/issues/11) · **部署前调研归档；实际部署记录见下方。**

> 后续已按用户要求在当前测试环境完成部署：权重先发布NFS、引擎先进入私有Registry，worker/GPU由GPUStack自动选择，1/1 Ready及三类真实图片调用通过。见[实际部署、故障恢复及清理记录](../../results/ovisocr3/20261118-deploy/REPORT.md)。下文保留部署前的调研基线与候选建议。

## 部署前建议

建议采用 **固定 OvisOCR3 仓库版本＋独立 vLLM 0.22.1 镜像＋单 GPU 单副本**。先验证单页结构化解析与指定字段抽取，再由 GPUStack 管理运行实例，最后接入已有 MaaS 统一任务接口。OvisOCR3 的整页模式不要求另加 Paddle/PaddleX 版面检测模型；跨页表格递归重建则有额外的渲染和编排成本，单独验收。

现有 RTX 4080 SUPER 16GB、compute capability 8.9、580.95.05 驱动具备试点的硬件条件，但尚无 OvisOCR3 初始化、CUDA 内核和峰值显存证据。**当前没有独立空闲的 Ready GPU**，不能沿用昨天“worker7 无实例”的旧观察。首选新增独立 worker；如需与 PP-OCRv4 共卡，先单独确定预算并验证混合负载，不停止已有服务换取容量。

## 本次现场基线

北京时间 **2026-11-18 18:09:17**，复用已有受信 SSH 通道，只读查询控制主机、GPUStack 管理面及 MaaS 当前目录。GPUStack 返回 `v2.2.3 / 1cfcbbf`。[脱敏证据](../../results/ovisocr3/20261118-research/evidence.json)

| 对象 | 实时读取结果 | 部署含义 |
| --- | --- | --- |
| worker6 / worker7 | Ready；均为 4080 SUPER 16GB、580.95.05；各运行一个 `maas-ocr` 实例，显存申报各 3.5GiB、遥测实际使用约 1.92GiB | 已承载 PP-OCRv4，不是空闲测试卡；余量不构成安全共卡证明 |
| worker8 | Ready；同型号/驱动；`unlimited-ocr-2-nfs` model13 / instance195，1/1；申报 12GiB，GPU 遥测约 10.35GiB | 保留现有 Unlimited 服务 |
| worker4 / worker5 | not_ready | 不计入可用容量 |
| 新 PC | x86_64、8逻辑CPU、约15.39GiB RAM；可用约10.51GiB，根盘可用约263.10GiB | 承担控制、任务和文件服务，不在共享宿主安装 GPU 推理依赖 |
| MaaS | 当前目录含 `ocr.default → maas-ocr`、`document.unlimited-ocr-2 → unlimited-ocr-2-nfs` | 新模型复用当前统一接入流程，不恢复旧独立文档执行器或旧本地权重部署 |

GPU 数据为管理面遥测，本轮没有直接登录目标 worker 检查 GPU UUID、`nvidia-smi` 进程、容器 GPU runtime 或容器内挂载；正式准备时须重新核对。新任务已使用统一 API/任务库和一个静态执行槽，历史文档入口保留查询和原键回执；旧 GPUStack model11 已退役。依据[当天切换与退役记录](../../results/maas-cutover-apply-20261118/REPORT.md)。

本轮只读取远端状态和公开元数据/小源码文件；没有创建容器、任务或模型，没有下载完整权重或运行镜像层，没有修改配置、NFS、路由、驱动或服务，也没有任何 VM 重启及等效操作。

## 固定模型与运行环境

| 项目 | 已核对值 |
| --- | --- |
| 模型 | `ATH-MaaS/OvisOCR3`，约0.8B；Hub元数据为 852,985,920 个 BF16 参数 |
| 固定 revision | `5b252386ba615bd464a5a26597560eaba87dae5c` |
| 权重文件 | `model.safetensors`，1,706,030,528 B，约1.589GiB |
| 发布方公布的权重 SHA-256 | `aa2311c6b22b07cf3afe9aac6e8bcb02a2b19dd8671b2a91d0b144071577e600`；本轮未下载完整文件验证 |
| 架构 | `Qwen3_5ForConditionalGeneration` / `qwen3_5`，含视觉编码与混合注意力 |
| 模型许可标识 | Apache-2.0；最终运行环境中的第三方依赖另保留其许可 |
| 官方示例 | `vllm==0.22.1`、Pillow、GPU；单张或两张页面图像、固定任务 prompt、关闭 thinking、GDN prefill 使用 Triton |
| 官方示例预算 | GPU比例0.8；输出最多16384 tokens；图像像素范围448²至2880²；没有显式设置上下文上限 |

模型参数、权重、tokenizer、processor、chat template、18份 `prompts/*.txt` 均须作为同一版本保存。`prompts/` 不影响基础引擎加载，但决定实际任务行为，不能仅下载权重。模型配置里的 Transformers 版本记录不是安装依赖锁；本次 PyPI 元数据表明 vLLM 0.22.1 依赖 `torch==2.11.0`，不能与 Unlimited 的定制 SGLang/其他模型环境混装。

来源：[固定模型卡](https://huggingface.co/ATH-MaaS/OvisOCR3/blob/5b252386ba615bd464a5a26597560eaba87dae5c/README.md)、[固定模型配置](https://huggingface.co/ATH-MaaS/OvisOCR3/blob/5b252386ba615bd464a5a26597560eaba87dae5c/config.json)、[vLLM 包元数据](https://pypi.org/pypi/vllm/0.22.1/json)。vLLM 的对应版本[明确列出该 Qwen3.5 多模态架构](https://docs.vllm.ai/en/v0.22.1/models/supported_models/)，仍须以本模型真实推理验证兼容性。

### 可复用的运行镜像

已只读取官方 `vllm/vllm-openai:v0.22.1` 的 registry manifest/config，尚未拉取或启动：

| 对象 | 固定值 |
| --- | --- |
| manifest index | `sha256:953d3a06d5e64ab582985cd7401289d3abf2a2c14ef2158e9a84313daeec77d7` |
| Linux amd64 manifest | `sha256:55c9bcee9fc66644b139fddae8a7a03e4c0c8a25ab5c64b0ce614554a8abf5d5` |
| config digest | `sha256:0204ba447a71c42f300e5218e1fe236f539f8b44da74198132366c3baec5887b` |
| amd64 压缩层合计 | 9,223,574,602 B，约8.59GiB；不是解压后占用 |
| config 中的 CUDA | **13.0.2**；不能用 vLLM 通用文档中的默认 CUDA 版本代替该产物事实 |
| 镜像 entrypoint | `["vllm", "serve"]`；镜像后直接填写模型路径及参数 |

来源：[官方镜像 tag 元数据](https://hub.docker.com/v2/repositories/vllm/vllm-openai/tags/v0.22.1)。先将固定 amd64 产物镜像到现有私仓并核验目标 digest；不使用浮动 `latest`。实际拉取后再验证 `vllm`、Torch、Transformers、CUDA 库、`pip check` 与所需命令参数。CPU 导入通过不代表 CUDA 内核或图像推理通过。

## 推荐部署顺序

1. **准备模型包：** 在 NFS 导出目录之外下载上述固定 revision，至少包含权重、全部 tokenizer/processor/config/template、任务 prompts 和 LICENSE；按完整文件清单、大小及 SHA-256 核验后，再发布不可变版本目录。不要原地覆盖运行中的版本。
2. **准备 GPU 容量和镜像：** 重新核对目标 UUID/进程、驱动、runtime、CPU/RAM、磁盘和端口。优先独立 worker；共卡必须先验证既有 PP-OCRv4 与新候选的启动、稳态和峰值组合。禁止为了试点升级驱动、重启 VM 或停掉已有模型。
3. **独立引擎试点：** 先完成单张图片、一个在执行请求的真实推理，再验证结构化解析与指定字段抽取。不一开始打开长 PDF、双图递归或高并发。
4. **GPUStack/Higress：** 固定独立 custom backend、模型名、镜像、模型路径、资源申报、worker选择和路由权限；从零副本准备，到一个已绑定副本，核验实际参数、挂载、健康及网关真实图片请求。
5. **MaaS：** 用新 adapter 接入已有 artifacts/tasks/result/usage 链路，通过隔离测试与实际下游验收后，再登记到统一任务目录。当前历史入口和历史任务归属继续保留。

模型存储应沿用当前 NFS：新PC导出根为 `/root/maas/data/modelsWeights`，worker宿主按需只读挂载 `/srv/maas/models`。建议版本目录为 `ovisocr3-5b252386`，容器内映射 `/models/ovisocr3:ro`；编译及运行缓存写 worker 本地独立目录。**宿主挂载成功不代表 GPUStack 创建的模型容器已看到同一目录**，须按实际部署检查映射；NFS来源、读取权限和故障行为也要验收。详见[当前存储约定](../framework/MODEL-WEIGHTS-NFS.md)。

### 单页低并发启动示意

**以下为待验证命令，未执行。** 仅用于已核验的测试 GPU 和宿主回环接口；模型与缓存目录须事先准备，端口须确认可用。GPUStack 原生部署阶段由它负责容器生命周期，不在同一GPU上额外启动重复实例。

```sh
: "${OVIS_GPU_UUID:?set the verified trial GPU UUID}"
: "${OVIS_MODEL_DIR:?set the complete fixed model directory}"
: "${OVIS_LOCAL_CACHE:?set an existing private local cache directory}"

docker run --rm --name ovisocr3-trial \
  --gpus "device=${OVIS_GPU_UUID}" \
  --memory 16g --memory-swap 16g --cpus 4 --pids-limit 256 --shm-size 4g \
  -p 127.0.0.1:18491:8000 \
  --mount "type=bind,src=${OVIS_MODEL_DIR},dst=/models/ovisocr3,readonly" \
  --mount "type=bind,src=${OVIS_LOCAL_CACHE},dst=/cache" \
  -e HF_HUB_OFFLINE=1 -e TRANSFORMERS_OFFLINE=1 \
  -e HF_HOME=/cache/hf -e VLLM_CACHE_ROOT=/cache/vllm -e TRITON_CACHE_DIR=/cache/triton \
  vllm/vllm-openai@sha256:55c9bcee9fc66644b139fddae8a7a03e4c0c8a25ab5c64b0ce614554a8abf5d5 \
  /models/ovisocr3 --served-model-name ovisocr3 \
  --host 0.0.0.0 --port 8000 --dtype bfloat16 \
  --max-model-len 16384 --max-num-batched-tokens 4096 --max-num-seqs 1 \
  --gpu-memory-utilization 0.50 --enforce-eager --gdn-prefill-backend triton \
  --limit-mm-per-prompt '{"image":1,"video":0}' \
  --mm-processor-kwargs '{"images_kwargs":{"min_pixels":200704,"max_pixels":2073600}}' \
  --default-chat-template-kwargs '{"enable_thinking":false}'
```

这是独立单页试点 profile：16GB卡上配置约8GiB的引擎预算、16K上下文、区域/页面并发1、最多1440²像素。**8GiB不是模型已测占用或最低需求**，CUDA初始化及临时张量也须采样；`--gpu-memory-utilization` 不构成显存硬隔离。前述官方 profile 的像素和输出预算更大，不能套用到本配置或声称其官方分数已复现。较低像素预算可能损失小字质量，较短输出可能截断密集页面，都需要单独对照。

显式设置上下文，避免沿用模型配置的262144最大位置数。当前命令选项已与 vLLM v0.22.1 源码核对，但没有在镜像中执行 `--help` 或启动；具体 kernel、JIT、挂载读写和限额仍未验。无需安装模型仓库任意远程 Python 代码；推理使用 vLLM 已有 Qwen3.5 实现。

### HTTP 调用与结果检查

先检查 `GET /health`、`GET /v1/models`，再通过 `POST /v1/chat/completions` 发送可信 PNG/JPEG 的 base64 data URL，图片后放固定任务 prompt。例如，任务正文从模型版本目录的 `prompts/kie_fields.txt` 读取，代入 JSON 编码的字段名数组；不要让文件名、字段名或用户问题经 shell 拼接执行。

请求使用 `model="ovisocr3"`、`stream=false`、`temperature=0`、`max_tokens=4096` 和 `chat_template_kwargs={"enable_thinking":false}`。该输出预算只用于首批短页和字段样本；图像 token、任务提示和输出必须一起放进16K上下文。指定字段的模型输出是 JSON 对象，完整解析页的输出则由所选 prompt 决定。

正常 HTTP/健康结果不能替代推理验收。核对唯一 choice、`finish_reason=stop`、非空内容和任务专属 schema；`length` 归为输出不完整，不清理后冒充成功。JSON模板只通过提示词约束，仍要校验字段、类型、大小、坐标和完整性，不把“可解析JSON”等同于字段正确。

首次客户端不得自动重试；若使用 OpenAI SDK，要显式限制其自动重试。客户端超时或断开不证明GPU已取消，恢复派发前需核对引擎活动。GPUStack/Higress自身的非幂等重试策略另行读取验证，不能从 MaaS `max_attempts=1` 推导 GPU 恰好执行一次。

## 功能范围、CPU与跨页成本

首试建议覆盖 `document_structured.txt`、`kie_fields.txt`，以 `plain_ocr.txt` 作诊断基线。后续按业务需要增加模板抽取、位置问答或图文描述，不为每种 prompt 另部署一份权重。模型 bbox 使用输入图像上的0–999整数坐标；裁剪输入的坐标相对裁剪图。位置问答的 bbox 是字符串，部分其他任务是整数数组，不能共用一个无验证的解析器。

本次所核官方部署示例明确要求 GPU；未发现该版本专门的 CPU/GGUF/量化部署验证。不能因基础架构能在其他后端加载就声称 OvisOCR3 的完整 CPU 服务已经可用。本轮优先原始 BF16 GPU 路线，CPU和量化单列后续调查，不把它们当当前资源回退手段。

跨页表格不是把整份 PDF 一次交给模型：官方方式先独立解析每页，再把相邻两页的图像和 Markdown交给同一模型重建续表。多于两页时从后向前递归，重建后还会重渲染更新页。`document_crosspage.py` 内部直接加载 vLLM，并依赖 BeautifulSoup、markdown-it-py、Playwright及Chromium；它不是现有 MaaS 可直接使用的远程客户端。

因此，跨页功能单独处理模型传输、浏览器生命周期、页关联、次数/时限及资源预算。官方 Stage 1 示例允许保留 `length` 结果并作清理，这与本系统“任一页不完整则任务失败”的契约不同，不能照抄为成功判定。图像引用只描述待裁剪区域，模型不会创建图片文件；首版不支持图片提取时，要明确标记省略，不能输出悬空下载路径。

来源：[官方跨页脚本](https://huggingface.co/ATH-MaaS/OvisOCR3/blob/5b252386ba615bd464a5a26597560eaba87dae5c/document_crosspage.py)、[论文的任务与坐标定义](https://arxiv.org/html/2610.33066v1#S2)。上述是部署机制，不是本地质量或长文档验收结论。

## MaaS 接入必须到达的位置

沿用[多模型接入流程](../../deploy/maas/MODEL-ONBOARDING.md)，建议新逻辑名 `document.ovisocr3`、专属 driver 和结果 schema。Ovis 的普通 OpenAI请求、固定任务 prompt、逐页/逐任务JSON与坐标处理不同于现有两个 adapter，不能仅把 Unlimited 的模型名替换掉，也不能包装成旧 PP-OCRv4 的行置信度结果。

| 实现位置 | 后续实际工作 |
| --- | --- |
| `maas_platform/adapters.py`、新模型 adapter/输出解析 | 注册协议，固定模型、prompt、profile、镜像和后处理版本；任务参数白名单；不任意透传客户端引擎参数 |
| `config.py`、`service.py` | 接入版本校验、允许的输入MIME、默认期限/尝试次数、上传和模型权限；复用PDF检查/渲染与artifact生命周期 |
| `api.py`、接口schema/示例 | 正确的 capabilities、输入/输出版本、预算和计量单位；不把新driver漏在旧字符串分支之外 |
| `worker.py`、`metering.py` | 纳入成功计量；明确页/图单位，内部crop、KIE字段数或跨页调用不额外计数；token口径未验证时明确 unavailable |
| `recovery.py`、UNKNOWN/容量配置 | 注册适合新模型的恢复探针，沿用已实现的隔离和证据确认；统一执行器仍为单槽，需评估长任务对短图排队的影响 |
| catalog/bindings、GPUStack backend及route | 新模型、固定版本和授权；不扩大既有调节器写入范围，不把调度claim当GPU硬限额 |
| adapter/契约/集成测试、OpenAPI | 非法输入、坏JSON、缺字段、越界bbox、截断、多页完整性、超时/取消、唯一计量、项目隔离、旧模型回归 |

正式接入复用统一任务库和HTTP入口；隔离试点使用受控独立环境，不建立新的永久用户队列或文档门户。新模型输出中的 HTML、LaTeX、Mermaid及图像引用按不可信文档处理；模板抽取不等于受约束解码，更不保证金额、税号等业务字段正确。

## 验证与交付边界

先固定一个短英文页、一个中文密集页、一个含表格/公式页及一个有标注的字段抽取样本；记录参数、像素、实际输入/输出token、初始化与推理峰值、延迟和错误。低资源 profile 与官方像素 profile 分开比较，字段按完全正确率与缺失/无依据生成检查，不用通用榜单分数代替。

单页和KIE通过后，验证串行多请求隔离、输出截断/非法JSON、客户端超时后资源收敛、原生调度及网关授权，再完成 MaaS 上传→任务→查询→结果→唯一收据。PDF和跨页模式另加页关联、两页有/无续表及三页递归样本；持续负载和共卡容量属于独立验收。

本次完成公开依赖与产物元数据、任务prompts、官方脚本和项目接入位置的静态核对，以及一次远端只读基线。完整镜像/权重拉取、包导入、CUDA kernel、GPU峰值、CPU运行、业务质量、网关与MaaS联验均未进行。001/002禁止VM重启的约束继续适用；不部署旧退役插件，不因001旧控制面角色阻止其普通worker注册。

静态校验通过：25份已读取的模型元数据/任务文件及4份vLLM源码的SHA-256一致，18份任务prompt齐全；启动示意通过`bash -n`，3组JSON参数可解析，本报告4处本地链接有效；公开文件未包含真实环境连接地址、完整GPU UUID或凭据。该校验没有执行启动命令。
