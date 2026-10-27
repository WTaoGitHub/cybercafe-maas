# GPUStack v2.3 调研与本系统复用评估

日期：2026-10-27。结论：值得安排独立升级验证，优先复用原生部署历史/回退、YAML部署模板、负载均衡和注册安全改进；不立即升级001或母版，不改变正在进行的新快照/新克隆验收基线。

## 1. 版本与证据边界

- 当前系统固定v2.2.3，对照源码commit为1cfcbbff02aacdb51f0a390378dd7bcddd9bf365。
- 正式v2.3.0标签为63a03ba137842153092749fbda1b670c96b10edf。官方发布页显示10月23日发布：[发布说明](https://github.com/gpustack/gpustack/releases/tag/v2.3.0)。检索列表可能仍缓存RC，不能据此判断尚未正式发布。
- 用户本地仓库HEAD为47bd0c134410ea89514116aee883aec03ee72996，描述v2.3.0-9-g47bd0c13，工作区干净。即“最新源码”含正式版之后9个提交，不能把HEAD全部能力归入v2.3.0。
- 下文功能依据本地git标签差异、源码和文档审查，未启动2.3环境，未执行真实升级、容器挂载/流量/千节点容量测试。未修改GPUStack仓库或服务器。
- [证据JSON](GPUSTACK-V2.3-RESEARCH-EVIDENCE.json)记录版本和关键文件是否在正式版后继续改变；本轮核对的10项关键源码/文档在v2.3.0与HEAD一致。

## 2. 主要变化与对我们的价值

| 能力 | 2.3的实现/变化 | 本系统应用及边界 | 建议 |
|---|---|---|---|
| 部署配置历史与回退 | 原生revision、比较、预览、回退、保留策略 | 替代未来自建“GPUStack部署配置历史”。不回退独立路由、MaaS目录、任务记录、结果、数据库、镜像内容或母版快照 | 高 |
| 部署YAML导入导出 | 多部署导出、导入预检和覆盖确认 | 用于模型部署模板、迁移和版本管理；不能视为整系统备份，也不打包模型权重/镜像/私仓凭据 | 高 |
| 新路由LB | round-robin、least-inflight、session-affinity和fallback插件，健康/拒绝控制；自动多副本部署路由默认启用least-load相关能力 | 优先复用健康实例分配；OCR优先验证least-inflight，不必开启会话亲和；不等于安全重放任务或E1完整排空 | 高 |
| API key鉴权优化 | 网关侧校验与授权缓存，减少逐请求访问server | 对高吞吐更有价值，需验证权限变更/吊销传播及MaaS现有key scope；不替代MaaS配额和任务计量 | 高 |
| 注册TLS与权限 | 私有CA引导、自签证书注册改进；insecure_tls显式开关；跨集群注册/worker管理等权限收紧 | 改善worker到原生GPUStack HTTPS，配置服务器仍有独立TLS/签名契约；保留校验，不以跳过TLS作为默认方案 | 高 |
| 按时段调整模型副本 | cron窗口、持续时间、基线副本、暂停/恢复 | 可匹配夜间供给计划，但只是模型副本目标，不能判断某台电脑是否有人使用或确保GPU已释放 | 中高 |
| OTA目录/后端元数据 | 更新模型目录、社区后端及版本列表，支持自定义源和内置离线内容 | 可复用元数据发布入口；不是模型权重CDN、镜像仓库、E5启动配置分发或下载调度器 | 中高 |
| 内置后端自定义镜像 | vLLM/SGLang可指定image_name并保留其原生调度能力 | 后续模型镜像封装更方便；现有RapidOCR自定义后端无需因此改成vLLM | 中 |
| 就绪与错误处理 | vLLM/SGLang使用/health；实例退出码、不可用时503、TUNNEL重连路由恢复等 | 有助于依赖原生状态，符合退役collector的方向；不代表任意自定义OCR后端都自动获得准确健康检查 | 中高 |
| 共享KV缓存与PD分离 | 独立缓存服务、跨副本/节点KV复用、prefill/decode角色调度和运行 | 面向LLM，对普通OCR和当前E5没有直接收益；KV缓存不是模型文件缓存或图片任务队列 | 后置 |
| GPU切分/GPU实例 | 切分资源部署、GPU实例计量、MIG映射等 | 不能据此声称RTX4070/4080获得硬件MIG，也不能据此解决供应商vGPU/PCI直通能力 | 后置 |
| 原生benchmark增强 | 并发/请求率扫描、SLO/最大吞吐/自定义模式、结果点分析 | 后续LLM容量测试可复用；需核对OCR请求形态，保留当前OCR和MaaS任务链路验收 | 中 |

以上不仅是模型列表更新。对本项目而言，最值得调整的设计是：将部署生命周期和推理路由的工作进一步下放原生GPUStack，MaaS专注客户任务、文件、配额和业务计量。

## 3. 四个必须看清的功能边界

### 3.1 部署回退不等于业务回退

源码：[model_revisions.py](/Users/4paradigm/dev/gpustack/gpustack/routes/model_revisions.py)、[server/model_revisions.py](/Users/4paradigm/dev/gpustack/gpustack/server/model_revisions.py)。使用与模型编辑同类的权限、锁和配置验证。

原生回退恢复部署配置，回退API本身不触发实例重启；UI默认另行请求重启。保存的新revision不保证所有运行实例已更新，也没有零停机承诺。默认保留最新外加10个旧版本；历史环境变量保留原值，可能含秘密。旧部署首次访问/修改时才补初始快照，无法凭空还原升级前丢失的历史。

因此本系统仍需自己的MaaS模型revision、客户结果版本、路由切换和验收记录；可以将它们关联到原生revision，而非混为一个ID。数据库升级后的回退应靠已验证备份恢复，不能简单换回旧镜像。

### 3.2 定时伸缩不等于节点维护

源码：[scaling_scheduler.py](/Users/4paradigm/dev/gpustack/gpustack/server/scaling_scheduler.py)、[schemas/models.py](/Users/4paradigm/dev/gpustack/gpustack/schemas/models.py)。平台GPUSTACK_TIMEZONE决定时间解释，没有逐模型时区；计划外采用baseline_replicas。

它改变模型副本数，不能替代逐worker排空、活动请求处理、模型实例完全退出确认、Windows切换或GPU归还。E1仍有必要，但应重新核对2.3路由健康控制可复用部分。

### 3.3 注册TLS不等于E5全部接入流程

源码：[worker/tls.py](/Users/4paradigm/dev/gpustack/gpustack/worker/tls.py)、[cacerts.py](/Users/4paradigm/dev/gpustack/gpustack/routes/cacerts.py)。worker可用注册命令提供的可信SHA256校验下载的CA bundle，再验证HTTPS和安装信任。校验摘要本身仍需通过可信配置提供；从同一未验证网络同时取证书和摘要不能建立身份保证。

GPUStack worker身份仍是原生记录和凭据，不等于GPU UUID；2.3没有自动替我们实现供应商快照的克隆身份隔离、tower角色判定、E5共享注册码分发、配置签名和管理员手动拉取脚本。E5仍负责“安全地启动正确worker”，之后由GPUStack纳管。

### 3.4 模型目录、权重、镜像和启动配置仍需区分

OTA更新元数据；YAML更新部署定义；原生worker负责模型下载与容器部署。我们继续复用原生私仓和拉取能力，避免另外实现分发调度器。

本轮源码审查不足以证明新runtime下model-storage-v1挂载契约完全兼容。worker身份排除、模型RO/cache RW、Docker socket不继承，必须对新版本实际容器再次测试；NFS、HF/ModelScope冷下载、缓存命中与清理、私仓凭据/证书持久化仍需独立验收。不能因升级就删除这些测试。

## 4. 千节点：有性能改进，没有容量结论

正式标签最后几次修复很值得关注：

- 1c383ece：worker list/watch资源预留共享缓存，避免对每个worker更新都读缓存服务RAM表。
- 63a03ba1：缓存服务provider目录共享快照、合并并发加载、跨server失效；提交说明指出2.3开发过程中每worker每分钟拉目录增加了DB查询，属于修复新增功能的开销，不能宣传为相对2.2.3的既定性能倍增。
- 33abe06d：没有对应workload类型时跳过孤儿资源查询。
- 286d1e75：内存跟踪改成显式启用。

这些变化表明要核对控制面查询放大和后台轮询，但不证明1000节点已被官方验收。本轮没有找到可以直接作为单Server千节点承诺的明确容量保证。E7保留：100/500/1000协议模拟、代表性真实GPU测试、集中上线、数据库/连接/内存与事件扇出、至少24小时稳态；分清控制流量与图片推理流量。升级2.3本身不能关闭E7。

## 5. 安全与升级兼容性

2.3正式发布包含worker管理鉴权、跨集群注册、worker代理身份归属、未授权下载/文件写入、秘密文件权限、跨组织provider/watch泄露等修复。这些与共享集群注册码和非可信clone场景直接相关，应作为升级评估的重要收益。不能把所有修复都简化成“我们的2.2.3已确认可被利用”；需按启用功能和具体路径评估。新接口权限更严格，也可能让现有管理脚本先前不正确的key用途暴露出来。

正式v2.3.0依赖：gpustack-runtime 0.2.6、runner 0.1.31.post4、higress-plugins 0.5.0.post2；当前我们的runtime为0.2.1，必须复测运行时行为。正式版requires-python仍为>=3.10,<3.13；本地HEAD后续31d37bfa才改成Python3.11起，不能把它写成正式2.3要求。后续main还新增65a01e5d的通用插件扩展契约，以及自定义URL路径修复，不纳入本次2.3交付判断。

官方本地升级文档：[Docker](/Users/4paradigm/dev/gpustack/docs/upgrade/docker.md)、[Cluster](/Users/4paradigm/dev/gpustack/docs/upgrade/cluster.md)。建议先备份，先server再workers，worker与server版本一致，不把main/RC与正式版混合升级；worker升级为容器重建，保留原参数/数据卷，并非VM重启。

对本项目另有要求：源镜像与runner/runtime/plugin固定digest；保留原生worker身份但不复制入母版；备份数据库/路由/权限/配置；完成恢复验证；E5签名发布版本、worker布局版本和母版快照版本分别登记。此调研没有取得或执行现网升级授权。

## 6. 建议的推进顺序（尚未实施）

1. 先完成当前2.2.3新快照克隆的自动注册及model-storage-v1验收，避免同时改变镜像和GPUStack版本。
2. 用独立数据目录/数据库和独立端口部署正式2.3.0验证环境，不能与001现网共用数据库或让同一worker被两个控制面争用。优先独立可销毁clone，001/002禁止VM重启的约束继续有效。
3. 验证最小闭环：test1类集群注册/可信TLS、实例凭据权限、现有OCR镜像拉取、RO/RW挂载隔离、同步/异步/文件/单次计量、停止与恢复。
4. 单独验收原生revision回退+MaaS目录切换、YAML往返、least-inflight与故障fallback、鉴权缓存吊销时效及维护行为；确认是否需要Redis等共享状态及各项策略的实际依赖。
5. 再决定现网维护窗口：先备份和server升级，后worker/母版升级，再保存管理员快照；实际完成前不改生产版本基线。E7另开容量验收，不与功能验证混用结论。

本报告是研究交付，不是路线图已获批准变更，也不表示E5/E1/E7自动完成。
