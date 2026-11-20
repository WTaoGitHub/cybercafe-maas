# Unlimited-OCR-2 候选镜像

此目录是隔离试点候选，尚不是生产部署。模型和 wheel 来源固定在 [执行计划](../../docs/framework/UNLIMITED-OCR-2-PLAN.md)及阶段报告中；不能复用旧 OCR driver。

[M1受控JSON试点已验收](../../results/unlimited-ocr-2/20261116-m1/REPORT.md)，最终镜像digest、运行参数、16GB单文档实测及边界见该报告。[GPUStack配置模板](gpustack-m1-template.json)需将数字/对象占位符替换为现场值并重新校验schema；先以零副本准备，单独设置路由ACL后才激活，不能把模板整个对象直接POST为一个资源。

构建前将固定模型 revision `0480966938291445591f69f08a13b041c073a96a` 中的 `wheel/sglang-0.5.20.dev13+g75b9b06b02-py3-none-any.whl` 放入本目录。Dockerfile 校验 wheel SHA-256，再按 `requirements.txt` 中版本解析安装，执行 pip check、补丁及模块导入。通过 `BASE_IMAGE` 传入受控 registry 的 CUDA 基础镜像完整 digest，避免在源码中保存私有 authority；当前基础 digest 为 `sha256:00fde7d4b087eb46c66aecebba22543c978f9071fbd0270a7a4c54d7da756dcb`。不得将凭据放入构建上下文。

补丁只在 wheel 内 v2 源文件原始 SHA-256 与预期一致时应用，在父类构造后删除旧语言塔并触发循环垃圾回收。它是社区问题的候选修复，必须通过 GPU 加载、重复启动和输出回归才能确认有效。

`requirements.txt` 记录候选环境的 Python 包版本，但尚未提供各分发包哈希或固定 apt 仓库快照；版本列表不是字节级可复现锁。构建产物应固定镜像 digest，未完成锁定包来源及独立复构验证前，不宣称源码可字节级复现。CUDA kernel、显存、输出质量及实际部署均需单独验收。

P2 初始化发现 Triton 编译需要 Python 开发头文件。`Dockerfile.python-headers` 在已验证候选镜像上单独追加 `python3-dev`；构建时以 `PARENT_IMAGE` 传入固定父镜像 digest 或已核验 image ID。隔离 GPU 容器中的最小 Triton JIT 算子已验证，完整模型 warmup 和真实解析仍需再次验证。

GPUStack v2.2.3 部署使用 `Dockerfile.resource-guard` 从已验证的头文件修复镜像追加启动守卫。构建参数 `PARENT_IMAGE` 和运行镜像均固定 digest。后端入口设为 `python3 /opt/maas/resource_guard.py`。固定版本 worker 通过 [MaaS bootstrap 的创建限额补丁](../../maas_bootstrap/worker_limits.py)，在 Docker 创建主推理容器时为匹配的镜像摘要和入口施加 16 GiB 内存、4 CPU 和零 swap。SGLang 启动守卫继续核对 cgroup v2 的内存与 CPU 上限，限额缺失时最多等待 120 秒后退出。

扩大部署前核对所有候选 worker 的补丁状态。验收新容器时核对实例归属、Docker `Memory=MemorySwap=17179869184`、`NanoCpus=4000000000`、`ShmSize=4294967296`，以及容器内 `memory.max`、`cpu.max`、`memory.swap.max=0`。Docker 事件应包含 `create` 和 `start`，守卫日志应确认通过。升级 worker 或推理镜像摘要后重新验收。启动守卫本地回归为 `python3 deploy/unlimited-ocr-2/test_resource_guard.py`。

保留 `restart_on_error=False`。GPUStack 创建或替换同一部署的容器时均使用该创建规则。处理未安装补丁的旧 worker 时，可在确认唯一容器的标签、镜像和挂载后按原流程手工设置限额，并让该 worker 完成受控升级。

构建运行镜像需依次执行两个阶段：先用 `Dockerfile` 和固定基础摘要构建候选，再用 `Dockerfile.python-headers`、以该候选的不可变 digest 或已核验 image ID 作为 `PARENT_IMAGE` 构建头文件层。不要直接部署第一阶段镜像；P2 运行使用第二阶段镜像。

`Dockerfile.processor-policy` 在固定资源守卫镜像上追加处理器约束。`patch_processor_policy.py` 校验两个上游源文件的 SHA-256 后，将客户端处理器解析限制为内置 `DeepseekOCRNoRepeatNGramLogitProcessor`：接受固定符号 `maas.unlimited-ocr-2.ngram.v1` 或旧内置类的严格匹配 JSON，不反序列化客户端对象；非法值在 tokenizer 调度前拒绝。构建内执行 `test_processor_policy.py`，包括禁止调用 `dill.loads` 的允许/拒绝用例。该限制作用于本候选镜像，不修改其他模型或共享 SGLang 后端。

`Dockerfile.pilot-policy` 从固定处理器约束镜像派生，将 M1 限定为 `stream=false`：Chat 入口在处理文档前拒绝 SSE，统一 tokenizer 也在调度前拒绝原生流式请求，两个源文件均有 SHA-256 守卫。此前 SSE 断连实测出现跨窗口 KV 残留，候选清理补丁未解决；本策略不声称修复了 SSE，而是按 M1 原定范围提供完整 JSON 文档响应。客户端超时不保证后台计算取消，必须等待并核实当前请求及 KV 释放后再派发；不自动重发。

`trusted_client.py` 使用上述固定符号，不需要安装 SGLang；实际发送时需要 `requests`。以固定模型的 `config.json`、按顺序排列的 PNG/JPEG 页面和明确的整文档输出预算运行；受保护网关的 Key 从 `UOCR_API_KEY` 环境变量读取，不写进源码。例如：

```sh
python3 trusted_client.py --url "$UOCR_GATEWAY_URL" --config /path/to/config.json \
  --output /private/result.txt --max-tokens 3000 /path/to/page-01.png /path/to/page-02.png
```

也可仅用 Python 标准库生成请求，再用 curl 验证；`UOCR_GATEWAY_URL` 是包含模型路由的实际推理入口，`UOCR_API_KEY` 使用已有授权 Key：

```sh
python3 trusted_client.py --config /path/to/config.json --request-json /private/request.json \
  --max-tokens 3000 /path/to/page-01.png /path/to/page-02.png
curl --silent --show-error --fail-with-body --noproxy '*' \
  --connect-timeout 10 --max-time 900 \
  -H "Authorization: Bearer $UOCR_API_KEY" -H 'Content-Type: application/json' \
  --data-binary @/private/request.json \
  "$UOCR_GATEWAY_URL/v1/chat/completions" > /private/response.json
```

curl 不配置自动重试。退出码0只说明HTTP/传输成功，仍须核对唯一 choice 的 `finish_reason=stop`、页标记数和内容完整性；`trusted_client.py` 的直接发送方式会检查完成原因、非空文本及页标记数。请求和输出包含文档内容，放在私有目录。

客户端限制1–11页，验证 JSON 完成、stop 和页标记数，并保留成功解析的原始文本；这不是质量验收或生产 PDF 入口。发送请求后的总期限为900秒（不含本地图片读取和编码），断连或超时不证明服务端停止：应先核对后端活动，不能自动重放。用户指定的 All models 权限保留，服务端处理器约束独立于 Key 的模型范围；客户端约束不能替代服务端校验。

## MaaS 文档执行保护试点

日常路由使用 [Dockerfile.execution-guard](Dockerfile.execution-guard)。在私有构建上下文中放入该 Dockerfile、本目录的 `resource_guard.py`、`uocr_execution.py` 及[共用回执客户端](../../gpu-ocr/execution_guard.py)，以固定 M1 镜像 `sha256:0a4ee2f9351225fe2ff91fe1d7f638b86972b9b2f704ab848325f2d0bc0d2fa6` 的完整地址作为 `BASE_IMAGE`。已验收的派生镜像摘要为 `sha256:9139bd41a0476782acee4be89363a5c1188c9910ed9211ce84d06920ac582615`。沿用原入口，创建时应用容器限额。

启动时核对 SGLang 的 Chat、调度器和 tokenizer 源码摘要。守卫要求每副本只见一张 GPU、只处理一份文档，保留窗口间 KV 复用，关闭窗口预取、重叠调度和启动时的自动推理。

配置 `MAAS_UOCR_EXECUTION_ORIGIN`、`MAAS_UOCR_EXECUTION_ROUTE`、`MAAS_UOCR_EXECUTION_KEY_FILE`，需要私有 CA 时配置 `MAAS_UOCR_EXECUTION_CA_FILE`。凭据通过私有只读文件挂载，并登记到现有 MaaS 的 `MAAS_PP_OCR_WORKER_KEYS_FILE`。受保护端只接受带 MaaS 尝试 token 的非流式文档调用。同路由的所有 GPUStack 目标和 MaaS 别名部署并验证后，才可开启目录里的 `execution_guard`。

GPU 进程在启动第一页前领取整份文档的执行权。已领取的文档在调用方断连后继续运行。所有窗口结束并发出文档 KV 释放指令后，再通过同一 IPC 通道询问调度器。调度队列、请求槽和 KV 池都已回收，并完成 CUDA 同步后，才提交终态。MaaS 收不到回执时保留 `UNKNOWN`。调度器未确认回收时，当前实例暂停接收新请求，需核实后恢复。

用 `python -m pytest tests/maas/test_uocr_execution.py` 回归执行权、窗口和调度器确认，用[现有任务库测试](../../tests/maas/test_document_tasks_integration.py)验证四页文档只派发一次并正确记账。
