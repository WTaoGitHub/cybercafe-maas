# Unlimited-OCR-2 候选镜像

此目录是隔离试点候选，尚不是生产部署。模型和 wheel 来源固定在 [执行计划](../../docs/framework/UNLIMITED-OCR-2-PLAN.md)及阶段报告中；不能复用旧 OCR driver。

[M1受控JSON试点已验收](../../results/unlimited-ocr-2/20261116-m1/REPORT.md)，最终镜像digest、运行参数、16GB单文档实测及边界见该报告。[GPUStack配置模板](gpustack-m1-template.json)需将数字/对象占位符替换为现场值并重新校验schema；先以零副本准备，单独设置路由ACL后才激活，不能把模板整个对象直接POST为一个资源。

构建前将固定模型 revision `0480966938291445591f69f08a13b041c073a96a` 中的 `wheel/sglang-0.5.20.dev13+g75b9b06b02-py3-none-any.whl` 放入本目录。Dockerfile 校验 wheel SHA-256，再按 `requirements.txt` 中版本解析安装，执行 pip check、补丁及模块导入。通过 `BASE_IMAGE` 传入受控 registry 的 CUDA 基础镜像完整 digest，避免在源码中保存私有 authority；当前基础 digest 为 `sha256:00fde7d4b087eb46c66aecebba22543c978f9071fbd0270a7a4c54d7da756dcb`。不得将凭据放入构建上下文。

补丁只在 wheel 内 v2 源文件原始 SHA-256 与预期一致时应用，在父类构造后删除旧语言塔并触发循环垃圾回收。它是社区问题的候选修复，必须通过 GPU 加载、重复启动和输出回归才能确认有效。

`requirements.txt` 记录候选环境的 Python 包版本，但尚未提供各分发包哈希或固定 apt 仓库快照；版本列表不是字节级可复现锁。构建产物应固定镜像 digest，未完成锁定包来源及独立复构验证前，不宣称源码可字节级复现。CUDA kernel、显存、输出质量及实际部署均需单独验收。

P2 初始化发现 Triton 编译需要 Python 开发头文件。`Dockerfile.python-headers` 在已验证候选镜像上单独追加 `python3-dev`；构建时以 `PARENT_IMAGE` 传入固定父镜像 digest 或已核验 image ID。隔离 GPU 容器中的最小 Triton JIT 算子已验证，完整模型 warmup 和真实解析仍需再次验证。

GPUStack v2.2.3 的试点部署使用 `Dockerfile.resource-guard` 从已验证的头文件修复镜像追加启动守卫；构建参数 `PARENT_IMAGE` 必须固定 digest。后端 entrypoint 同样设为 `python3 /opt/maas/resource_guard.py`，其余 SGLang 参数沿用已验证配置。守卫最多等待120秒，只有自己的 cgroup v2 `memory.max` 不超过16 GiB、CPU quota 不超过4 CPU时才执行 SGLang，缺失或无限额时超时退出。守卫的轻量 Python 等待过程发生在设置限额之前；限额保障覆盖模型启动、加载和推理，GPUStack 没有原生提供容器创建时的这两个参数。

每次创建新试点实例，先依据最新 instance ID/name、model ID、worker 身份、runtime workload/component 标签、镜像和挂载核对唯一容器，再由受控宿主执行 `docker update --memory 16g --memory-swap 16g --cpus 4 "$TRIAL_CONTAINER_ID"`。读回 Docker 配置、容器 cgroup 与守卫日志后才等待引擎健康；其他容器不在操作范围。独立无GPU测试已确认：无限额时守卫等待，设置16 GiB/4 CPU后才执行 SGLang `--help`。本地回归为 `python3 deploy/unlimited-ocr-2/test_resource_guard.py`。

每个新容器都需要上述受控操作；试点保持 `restart_on_error=False`，不提供无人值守的新容器恢复。重新创建/替换实例时重复核对和设置，不能仅复用旧容器的限额证据。进入无人值守发布前须接入经验证的 runner 原生限额或自动执行闭环。

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

在固定 M1 镜像中，把本目录的 `resource_guard.py`、`uocr_execution.py` 及[共用回执客户端](../../gpu-ocr/execution_guard.py)以只读文件挂载到 `/opt/maas/`。沿用原入口和容器限额。启动时核对 SGLang 的 Chat、调度器和 tokenizer 源码摘要。守卫要求每副本只见一张 GPU、只处理一份文档，保留窗口间 KV 复用，关闭窗口预取、重叠调度和启动时的自动推理。

配置 `MAAS_UOCR_EXECUTION_ORIGIN`、`MAAS_UOCR_EXECUTION_ROUTE`、`MAAS_UOCR_EXECUTION_KEY_FILE`，需要私有 CA 时配置 `MAAS_UOCR_EXECUTION_CA_FILE`。凭据通过私有只读文件挂载，并登记到现有 MaaS 的 `MAAS_PP_OCR_WORKER_KEYS_FILE`。受保护端只接受带 MaaS 尝试 token 的非流式文档调用。同路由的所有 GPUStack 目标和 MaaS 别名部署并验证后，才可开启目录里的 `execution_guard`。

GPU 进程在启动第一页前领取整份文档的执行权。已领取的文档在调用方断连后继续运行。所有窗口结束并发出文档 KV 释放指令后，再通过同一 IPC 通道询问调度器。调度队列、请求槽和 KV 池都已回收，并完成 CUDA 同步后，才提交终态。MaaS 收不到回执时保留 `UNKNOWN`。调度器未确认回收时，当前实例暂停接收新请求，需核实后恢复。

用 `python -m pytest tests/maas/test_uocr_execution.py` 回归执行权、窗口和调度器确认，用[现有任务库测试](../../tests/maas/test_document_tasks_integration.py)验证四页文档只派发一次并正确记账。
