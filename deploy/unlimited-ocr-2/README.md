# Unlimited-OCR-2 候选镜像

此目录是隔离试点候选，尚不是生产部署。模型和 wheel 来源固定在 [执行计划](../../docs/framework/UNLIMITED-OCR-2-PLAN.md)及阶段报告中；不能复用旧 OCR driver。

构建前将固定模型 revision `0480966938291445591f69f08a13b041c073a96a` 中的 `wheel/sglang-0.5.20.dev13+g75b9b06b02-py3-none-any.whl` 放入本目录。Dockerfile 校验 wheel SHA-256，再按 `requirements.txt` 中版本解析安装，执行 pip check、补丁及模块导入。通过 `BASE_IMAGE` 传入受控 registry 的 CUDA 基础镜像完整 digest，避免在源码中保存私有 authority；当前基础 digest 为 `sha256:00fde7d4b087eb46c66aecebba22543c978f9071fbd0270a7a4c54d7da756dcb`。不得将凭据放入构建上下文。

补丁只在 wheel 内 v2 源文件原始 SHA-256 与预期一致时应用，在父类构造后删除旧语言塔并触发循环垃圾回收。它是社区问题的候选修复，必须通过 GPU 加载、重复启动和输出回归才能确认有效。

`requirements.txt` 记录候选环境的 Python 包版本，但尚未提供各分发包哈希或固定 apt 仓库快照；版本列表不是字节级可复现锁。构建产物应固定镜像 digest，未完成锁定包来源及独立复构验证前，不宣称源码可字节级复现。CUDA kernel、显存、输出质量及实际部署均需单独验收。

P2 初始化发现 Triton 编译需要 Python 开发头文件。`Dockerfile.python-headers` 在已验证候选镜像上单独追加 `python3-dev`；构建时以 `PARENT_IMAGE` 传入固定父镜像 digest 或已核验 image ID。隔离 GPU 容器中的最小 Triton JIT 算子已验证，完整模型 warmup 和真实解析仍需再次验证。

GPUStack v2.2.3 的试点部署使用 `Dockerfile.resource-guard` 从已验证的头文件修复镜像追加启动守卫；构建参数 `PARENT_IMAGE` 必须固定 digest。后端 entrypoint 同样设为 `python3 /opt/maas/resource_guard.py`，其余 SGLang 参数沿用已验证配置。守卫最多等待120秒，只有自己的 cgroup v2 `memory.max` 不超过16 GiB、CPU quota 不超过4 CPU时才执行 SGLang，缺失或无限额时超时退出。守卫的轻量 Python 等待过程发生在设置限额之前；限额保障覆盖模型启动、加载和推理，GPUStack 没有原生提供容器创建时的这两个参数。

每次创建新试点实例，先依据最新 instance ID/name、model ID、worker 身份、runtime workload/component 标签、镜像和挂载核对唯一容器，再由受控宿主执行 `docker update --memory 16g --memory-swap 16g --cpus 4 "$TRIAL_CONTAINER_ID"`。读回 Docker 配置、容器 cgroup 与守卫日志后才等待引擎健康；其他容器不在操作范围。独立无GPU测试已确认：无限额时守卫等待，设置16 GiB/4 CPU后才执行 SGLang `--help`。本地回归为 `python3 deploy/unlimited-ocr-2/test_resource_guard.py`。

每个新容器都需要上述受控操作；试点保持 `restart_on_error=False`，不提供无人值守的新容器恢复。重新创建/替换实例时重复核对和设置，不能仅复用旧容器的限额证据。进入无人值守发布前须接入经验证的 runner 原生限额或自动执行闭环。

构建运行镜像需依次执行两个阶段：先用 `Dockerfile` 和固定基础摘要构建候选，再用 `Dockerfile.python-headers`、以该候选的不可变 digest 或已核验 image ID 作为 `PARENT_IMAGE` 构建头文件层。不要直接部署第一阶段镜像；P2 运行使用第二阶段镜像。

`trusted_client.py` 供受信内部试点使用，按固定 wheel 生成处理器，不接受外部处理器字符串。以固定模型的 `config.json`、按顺序排列的 PNG/JPEG 页面和明确的整文档输出预算运行；受保护网关的试点 Key 从 `UOCR_API_KEY` 环境变量读取，不写进命令行。例如：

```sh
python3 trusted_client.py --url "$UOCR_GATEWAY_URL" --config /path/to/config.json \
  --output /private/result.txt --max-tokens 3000 /path/to/page-01.png /path/to/page-02.png
```

客户端限制1–11页，验证 SSE 完成、stop 和页标记数，并在错误时保留已收到的原始文本；这不是质量验收或生产 PDF 入口。发送请求后的总期限为900秒（不含本地图片读取和编码），断连或超时不证明服务端停止：应先核对后端活动，不能自动重放。模型原始路由只允许受信试点身份，不能把客户端约束当成网关权限控制。
