# E1：逐 worker 隔离候选设计

状态：本地开发，不部署。P4服务器、正式Compose、数据库迁移、collector、API与执行器入口保持原样，今晚耐久测试仍验证P4版本。E1本地通过不等于GPUStack实机验收。

## 路由决策

GPUStack v2.2.3的维护状态用于调度过滤，不能独立证明所有既有模型路由已经撤销，更不能证明GPU进程结束。当前通用模型入口还有跨实例重试，任务层无法据此缩小UNKNOWN范围。

候选方案复用GPUStack的认证、模型部署、worker_selector与模型路由，为每个模型/worker创建独立别名及独立不可变pin。每个别名必须唯一匹配一个worker、只有一个本地running实例、相同语义revision和内容镜像，禁止分布式实例、fallback及网关重试。框架只在已验证别名中选路，不直接使用worker地址，不将内部 `X-GPUStack-Model-Instance` 当作对外选路接口。

这些是未来部署的必要条件，不是假定现有别名已经满足条件。别名会改变模型配置及资源占用，需要在P4验收后单独设计迁移窗口和实机容量验证；不能直接把现有多worker pin改成单worker。

源码依据来自本机 `/Users/4paradigm/dev/gpustack` 的 **v2.2.3 tag**（不使用当前main的额外能力）：

- `gpustack/policies/worker_filters/status_filter.py`：调度候选要求READY。
- `gpustack/policies/worker_filters/label_matching_filter.py`：复用worker标签选择。
- `gpustack/server/controllers.py` 的模型registry构建：收集有地址且RUNNING的模型实例；不能把单个maintenance操作当作完整撤路协议。
- `gpustack/gateway/utils.py`：内部instance header由网关注入，不是本方案依赖的公开控制API。
- `gpustack/server/worker_instance_cleaner.py`：维护状态影响清理行为，不代表停止推理进程。

## 拓扑与剩余容量

新增 `maas_platform/e1/topology.py` 独立验证每个别名，沿用现有VersionPin配置与镜像校验，不削弱P4 collector。要求新鲜的实际网关目标和禁重试证据。一次刷新只发布本轮验证成功的子集，不保留失败别名的旧证明；模型1的某别名失败不撤销模型2的健康别名。

容量来自显式批准的slots（首版每别名1），按逻辑模型汇总健康别名；同时仍须受全局、项目额度限制。slots是并发准入预算，不是性能推算。没有健康别名时仅对应模型不可执行，结果查询不受影响。

## 开发与发布隔离

E1代码独立模块，默认不导入现有P4服务路径；仅执行本地测试。P4后必须完成入口对接、单worker别名实际部署/撤路证据、所有旁路封闭和持续流量故障演练，才能启用局部UNKNOWN隔离。未关联的旧请求继续全局保守隔离。
