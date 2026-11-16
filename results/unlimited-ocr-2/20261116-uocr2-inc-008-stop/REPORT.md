# Unlimited-OCR-2：按故障次数上限停止

2026-11-16 · [Issue #11](https://github.com/WTaoGitHub/cybercafe-maas/issues/11) · [执行计划](../../../docs/framework/UNLIMITED-OCR-2-PLAN.md)

本轮停在 P3 / UOCR2-INC-008，未完成 M1。子代理做过一次安全定位，主代理两次处理后仍未取得完整的网关不重放证据，按用户约定停止实施；没有继续第三次修复。P4–P6 的 MaaS PDF/文档 driver、页计量和业务容量验收未开始。

## 已取得的证据

- CUDA 基础镜像、固定依赖、模型19文件、gc候选补丁及完整服务镜像已构建、校验并保存到新 PC registry。守卫镜像 manifest 为 `sha256:6e7145c45fe1673912451c3734799fca22d00965a71f43a9519c79b79fb52169`，config 为 `sha256:0f1e8e1564f98a23a1c0f50f5530ff7b86caae9b48c69afcd2e9352f17a6aa10`，19层，worker reader 实际拉取匹配。
- RTX 4080 SUPER 16376 MiB 在 BF16、单请求、窗口3、CUDA graph关闭的配置下，单页、2/10/11页、旋转、中英混排、简单表格/公式和串行隔离的合成样本通过；容器再启动后的单页输出逐字节一致。全程2秒采样观测显存峰值10776 MiB，不是精确瞬时峰值、业务质量或并发容量结论。详见[P2](../20261116-p2/REPORT.md)。
- GPUStack 管理实例曾进入 Running，实际16 GiB RAM/4CPU/禁swap/4 GiB shm/只读模型挂载通过，managed直连单页和双页与P2基线逐字节一致。UOCR2-INC-004头文件由子代理一次修复，UOCR2-INC-005启动资源守卫和UOCR2-INC-007镜像authority各由主代理一次处理成功。详见[运行限额证据](../20261116-p3-image-authority/REPORT.md)与[managed直连报告](../20261116-p3-managed/REPORT.md)。

## UOCR2-INC-008 两次处理与未验边界

实际 Higress route8 曾指向实例186的健康后端；现场 GPUStack v2.2.3 生成的 ingress 注解显式启用5xx、非幂等和指定状态码重试，实际 Envoy `num_retries=2`。这是本部署生成的策略，不是 Higress 产品默认；“2”是符合条件时额外重试的上限，不是实际重复执行计数。来源与官方语义见[归因核对](../20261116-p3-managed/REPORT.md#路由与阻塞)。发现后暂停了真实文档的网关测试。

1. 第一次：仅匹配该路由的独立 EnvoyFilter 尝试合并 `num_retries=0`。实际字段未变为0，撤销并确认原配置恢复。
2. 第二次：独立覆盖在该模型的17条API路由生效，去除5xx/非幂等重试条件，仅保留无任何 matcher 的 `retriable-headers`，并剥除请求重试控制头、固定 `x-envoy-max-retries=0`。其他路由完整对象未变。原 `num_retries` 数字仍为2，不能声称其直接变成0。随后临时将试点路由指向隔离503计数stub验证：第一个客户端在12秒时超时，stub最终只记录1个POST，第二组带控制头的请求未执行，不能据此承诺完整的不重放语义或持久性。临时探针覆盖已在finally撤销并核验恢复，stub按180秒上限退出且端口释放。

本轮未向 Higress 发送真实 OCR 文档，没有将配置读回、Running或直连成功冒充网关验收。实例替换、SSE/总时限、失败重试与下游完整文档链路仍未通过。原始请求的处理器使用 `dill.loads`，客户端固定处理器不等于服务端约束；后续发布前必须结合最终权限预期解决服务端边界。

后续复核发现：旧 `ocr.default` 使用相同网关策略，P0早已实测合成503触发3次后端调用；既有设计允许在无法关闭透明重试时登记上游边界并限制业务重试。本轮把“必须关闭网关重试”设为硬前置，未复用该基线，判断过严。上述两次尝试未通过仍是事实，但不等于已证实新模型发生重复执行，也不等于网关非零重试本身就是新模型部署故障。详见[旧基线复核](../20261116-p3-managed/REPORT.md#与旧-ocr-基线的复核)。

## All models 与收尾状态

用户确认，执行期间主动将既有 forDownStream Key 改为 All models。这解释了收尾读到的 `allowed_model_names=[]`，不是已证实的异常回写。已保留用户设置，没有在收尾覆盖。此前[UOCR2-INC-006报告](../20261116-p3-auth/REPORT.md)的Key缩限与拒绝结果仅为当时快照，不能作为当前或未来发布的权限预期；以后不得以收窄此Key来满足试点隔离。

收尾已验证：

- model11 为0/0副本、无实例；新 route8、试点 Key19及其明文文件已删除，两项本次 EnvoyFilter 均已撤销，既有全局 filter 保留。
- worker6 只有原 GPUStack worker 容器在运行，GPU compute process为0、显存1 MiB；503 stub端口已释放。客户端均已退出，内部队列在停止前未独立确认；停止后已确认试点进程终止，不能记作通过了无在途实例替换测试。
- backend14及model11零副本候选、模型只读文件、registry镜像和私有诊断证据保留；没有清共享缓存或重启VM、Docker引擎、worker。
- 原路由仍为原有两条，正式 `maas-ocr` 仍2/2。使用保持All models的同一旧Key，从实际 `kc-e2e-aliyun-slave3-server` 完成收尾 OCR，任务 `task_5a4af5e2c9284cffa622a7d88a3c2292` 为 SUCCEEDED，中文/英文内容正确，`successful_images=1`。

继续执行须由用户明确重新授权，先读取本检查点并复核现场；不能自动重放本轮未知结果或把故障计数重置后继续。下一项先按P0/既有设计评估新模型的重试契约：优先使用可用配置禁用透明重试；无法关闭时，验证并登记实际条件和上游尝试预算、应用层不叠加重放、模糊完成隔离及成功收据唯一。新模型的会话/副作用语义仍须验证，不仅凭非零retry配置判失败；同时按用户All models预期验证授权与服务端处理器边界。
