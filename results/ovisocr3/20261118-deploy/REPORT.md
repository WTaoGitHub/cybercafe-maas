# OvisOCR3 当前测试环境部署

2026-11-18 · [部署跟踪 #11](https://github.com/WTaoGitHub/cybercafe-maas/issues/11) · **已部署，1/1 Ready，已通过真实图片网关调用。**

按用户本轮要求，权重先发布到 NFS、引擎先发布到私有 Registry；GPUStack 部署保持 `worker_selector={}`、`gpu_selector=null`，由原生调度器选择放置。实际落到 worker7，与 PP-OCRv4 共卡。没有设置 worker 名称、GPU UUID、GPU ID 或 GPU 型号选择器。

本轮交付 GPUStack/Higress 模型服务。调用使用原有认证入口的 `POST /v1/chat/completions`，`model="ovisocr3"`，图片使用 base64 data URL，并附固定任务 prompt。MaaS 统一任务目录及专属 adapter 仍是后续接入工作。

## 已发布资产与配置

| 对象 | 实际结果 |
| --- | --- |
| 模型 | `ATH-MaaS/OvisOCR3`，revision `5b252386ba615bd464a5a26597560eaba87dae5c` |
| NFS 服务端 | `/root/maas/data/modelsWeights/ovisocr3-5b252386`；37文件，共1,732,391,626 bytes，含18份任务prompt |
| 权重 | `model.safetensors`，1,706,030,528 bytes；SHA-256 `aa2311c6b22b07cf3afe9aac6e8bcb02a2b19dd8671b2a91d0b144071577e600` |
| worker/推理容器路径 | `/srv/maas/models/ovisocr3-5b252386`；实际只读NFS4，推理容器内完整权重SHA-256复核通过 |
| 基础引擎 | 官方 `vllm/vllm-openai:v0.22.1` Linux amd64；私仓 `maas/vllm-openai`，manifest `sha256:55c9bcee9fc66644b139fddae8a7a03e4c0c8a25ab5c64b0ce614554a8abf5d5` |
| 运行引擎 | 私仓 `maas/ovisocr3-vllm`；amd64 manifest `sha256:34ab50787af81b270477f49457ea8f8162eec231f7266ce9d6552c19a39e6b20` |
| 运行镜像config | `sha256:17d04c74b424d324e9d1144c8bc85e073d79a7946c30561b174f333c874bc5ab`；33层，压缩层合计9,223,576,034 bytes |
| 实际软件 | vLLM0.22.1、Torch2.11.0+cu130、Transformers5.10.2、FlashInfer0.6.11.post2、CUDA13.0.2；宿主驱动580.95.05 |
| GPUStack对象 | backend16 `OvisOCR3-vLLM-custom` / `0.22.1-guard-r1`；model14 `ovisocr3`；instance199；route12 |
| 路由权限 | `allowed_principals`，没有新增额外principal；原有受权推理key调用成功，无认证请求返回401 |

完整模型包在共享目录外下载，按官方固定revision元数据逐文件验证大小、LFS SHA-256或Git blob ID，再生成全部文件的SHA-256清单，以不覆盖目标的目录重命名发布。没有原地覆盖其他模型，也没有在worker复制一份本地权重。

控制主机直连上游超时，先用回环SSH代理转发，随后通过可直达镜像通道传输大文件。所有文件仍按官方固定版本摘要验证；基础镜像通过`--preserve-digests`保持原manifest，已核对配置和全部镜像层。小文件由官方源补齐。见[模型清单](model-manifest.json)、[引擎发布证据](engine-publication.json)。

派生镜像只加入资源守卫和vLLM入口，复用仓库已有SGLang部署的纯Python cgroup检查函数，不安装或升级推理依赖。镜像实际包含一个上游依赖告警：`pygobject 3.42.1 requires pycairo, which is not installed.` **完整`pip check`未通过。** 此项保留，Torch/Transformers/vLLM导入、Qwen3.5架构注册及真实GPU推理分别通过，没有为OCR安装GTK依赖。

## NFS可见性与启动恢复

worker6/7原先在`/srv/maas/models-nfs`挂载共享export，而GPUStack worker映射的`/srv/maas/models`仍是本地目录。为两台旧worker安装并启用单模型只读NFS挂载：`/srv/maas/models/ovisocr3-5b252386`。这保留了原本地模型目录；新NFS worker的标准模型根已有共享export。没有改NFS export、访问名单、母版或驱动。

旧worker的`rprivate`绑定看不到后加的宿主子挂载。此次最初直接重启worker容器，复用了已消耗的单次启动许可，导致注册403；PP-OCRv4管理面一度为0/2，**不能把本轮维护写成全程无影响**。随后核验worker已停止、GPU与旧会话绑定，按既有fence/approve/Bootstrap流程取得新会话，清理已退出容器的旧身份并受控重建，保持原worker ID6/7和GPU UUID。两个VM启动ID均未变化。重复Bootstrap `apply`返回`UNCHANGED`。

恢复过程中原生调度器替换了PP-OCRv4实例，现为197/198，目标仍为2；原193/194的推理容器已由原生流程移除。一次错误放置到标准NFS worker8的实例196，因缺少`image-contained/model` marker而失败，已删除该错误实例；marker兼容问题另由[Issue #64](https://github.com/WTaoGitHub/cybercafe-maas/issues/64)跟踪。本轮恢复原服务，没有增加marker发布功能。

最终PP-OCRv4为2/2 Ready，并通过一次真实图片回归；Unlimited NFS实例195保持1/1 Ready。两者的部署配置和副本目标与本轮基线一致。最终只读检查MaaS健康为200、任务库`UNKNOWN=0`。此次仅进行了进程/容器级操作，没有VM重启、等效重启或旧退役插件部署。

## 运行profile与实测

BF16、16K上下文、每请求1图、最大1440²像素、`max_num_seqs=1`、`gpu_memory_utilization=0.50`、eager模式、Triton GDN prefill，关闭thinking。初始请求输出上限4096 tokens。GPUStack显存申报8GiB；它和vLLM比例均不是GPU显存硬隔离。

轻量启动守卫在vLLM加载前等待Docker限额。调度落定后只核对实际容器及其GPU、镜像、NFS来源，然后施加16GiB RAM、4 CPU、swap0；共享内存4GiB。容器cgroup读回通过。未给部署设置任何worker/GPU选择器。

| 观察项 | 结果与范围 |
| --- | --- |
| vLLM权重加载日志 | NFS4，checkpoint1.59GiB，加载约10.34秒；模型加载阶段报告GPU内存1.72GiB，不含后续KV池等完整服务占用 |
| KV cache | 当前profile分配约5.6GiB，实际执行并发限制仍为1 |
| 模型GPU进程采样峰值 | **8076MiB，约7.89GiB**，包含KV cache；初始化后采样276次，间隔配置1秒，无采样错误 |
| 同卡整卡采样峰值 | 8395MiB；当时PP-OCRv4没有并行压测负载 |
| 容器生命周期RAM峰值 | 6,142,025,728 bytes，约5.72GiB；cgroup `oom`/`oom_kill`均为0 |

这些数据属于本配置和这张合成页面，不能解释为最低显存需求、并发容量或业务准确率。GPU采样未覆盖最早初始化阶段，低资源profile也不等同于官方更大像素/输出预算。

## 实际调用验收

复用原推理凭据，经已认证Higress路由调用。输入为仓库新增的1280×900英文合成报告，无个人或生产数据。固定prompt、`temperature=0`、`stream=false`，客户端没有自动重试。

| 任务 | HTTP / finish_reason | 耗时 | 验证 |
| --- | --- | --- | --- |
| 普通OCR | 200 / stop | 3.015秒 | 标题、报告号、128、12 minutes均命中 |
| 指定字段抽取 | 200 / stop | 0.742秒 | 4个指定键及值全部与合成样本一致 |
| 结构化解析 | 200 / stop | 3.150秒 | 非空JSON数组，3元素；类别、内容类型及0–999 bbox校验通过 |
| PP-OCRv4回归 | 200 / stop | 0.638秒 | 同一合成图的4处预期文本均命中 |
| 无认证请求 | 401 | — | 被拒绝 |

`/health`、`/v1/models`及PID1实际模型路径核对通过；容器内离线环境变量有效，镜像RepoDigest与私仓固定manifest一致，完整权重SHA-256与官方固定版本一致。[结构化证据](VERIFICATION.json) · [可复用测试资产](../../../tests/ovisocr3/README.md)

## 故障与处理边界

| 编号 | 结果 |
| --- | --- |
| OVIS3-INC-001 | 直连上游超时；使用受控回环代理和可直达镜像通道，官方摘要校验通过，传输完成后关闭转发 |
| OVIS3-INC-002 | 直接重启worker复用已消费许可；按原会话fence和新许可恢复。首次恢复因授权目录缺失中止，补齐目录后完成；保持原worker ID/GPU和VM启动ID |
| OVIS3-INC-003 | PP-OCRv4重调度到标准NFS worker后缺marker；恢复2/2及真实调用，根因后续由#64处理 |
| OVIS3-INC-004 | 官方镜像的PyGObject/pycairo依赖告警保留，完整pip检查不通过；核心导入及本轮GPU功能通过 |
| OVIS3-INC-005 | 产物读取器最初只接受单manifest，OCI index读取404；补齐index/child读取后固定实际amd64摘要，没有为此修改镜像内容 |

## 运维与清理

实际运行配置与固定镜像引用保存在控制主机`/srv/cybercafe-maas/control/ovisocr3-20261118`；worker保留`/var/lib/maas-ovisocr3`下的挂载/恢复/资源限额回执。公开的[配置模板与操作说明](../../../deploy/ovisocr3/README.md)不包含真实连接地址或凭据。

临时检查容器、下载和采样进程已退出；SSH转发端口已释放。已清除6个本轮中断上传目录（962,955,559 bytes）、控制主机的构建镜像tag及36条精确选定的BuildKit缓存记录；没有执行全局prune或删除Registry已发布模型层。一次性源码、请求、响应、日志及旧身份备份共删除Mac112份、控制主机52份、两个worker27/21份，专属临时目录均已移除。复用价值的测试和合成图纳入仓库。请求保留的NFS权重、Registry镜像、模型服务及兼容挂载作为部署资产保留；清理后重新验证了私仓manifest及全部镜像层。

没有创建独立测试库、MaaS测试任务、测试账号或对象存储fixture；真实部署、启动接续和正常验证调用的审计记录保留。未做VM启动挂载验收、持续/并发混合负载、中文业务质量、CPU/量化推理、PDF/跨页编排或MaaS adapter接入。当前关闭出错自动重启，替换推理容器须重新验证并施加资源限额。
